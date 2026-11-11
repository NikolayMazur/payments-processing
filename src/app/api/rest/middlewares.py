import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.app_layer import metrics
from app.config import config
from app.context import request_id_var
from app.errors import PAYLOAD_TOO_LARGE_ERROR

SERVICE_PATHS = frozenset({"/healthcheck", "/metrics"})
MAX_REQUEST_ID_LENGTH = 128
UNMATCHED_PATH = "unmatched"


def get_path_template(request: Request) -> str:
    """Возвращает путь с именами параметров вместо значений.

    В метку метрики нельзя класть сам адрес: каждый payment_id породил бы отдельный ряд.
    """
    if request.scope.get("route") is None:
        return UNMATCHED_PATH
    path: str = request.url.path
    for name, value in request.path_params.items():
        path = path.replace(str(value), f"{{{name}}}")
    return path


async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Назначает запросу идентификатор и считает метрики ответа."""
    request_id = request.headers.get(config.API.REQUEST_ID_HEADER, "")[:MAX_REQUEST_ID_LENGTH] or str(uuid4())
    token = request_id_var.set(request_id)
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers[config.API.REQUEST_ID_HEADER] = request_id

    path = get_path_template(request)
    if path not in SERVICE_PATHS:
        metrics.http_requests.labels(method=request.method, path=path, status=response.status_code).inc()
        metrics.http_request_duration.labels(method=request.method, path=path).observe(
            time.perf_counter() - started_at,
        )
    return response


class BodySizeLimitMiddleware:
    """Отклоняет слишком большое тело запроса до того, как оно целиком окажется в памяти."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = dict(scope["headers"]).get(b"content-length", b"")
        if content_length.isdigit() and int(content_length) > self._max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        exceeded = False

        async def limited_receive() -> Message:
            # Content-Length может отсутствовать или врать, поэтому считаем и то, что реально пришло.
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    # Дальше тело не читаем: приложению отдаём конец потока, а его ответ ниже заменяем на 413.
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            if not exceeded:
                await send(message)
            elif message["type"] == "http.response.start":
                await self._reject(scope, receive, send)

        await self._app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": PAYLOAD_TOO_LARGE_ERROR},
        )
        await response(scope, receive, send)


def init_middlewares(app: FastAPI) -> None:
    app.middleware("http")(request_context_middleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=config.API.MAX_BODY_BYTES)
