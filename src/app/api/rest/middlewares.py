import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app.app_layer import metrics
from app.config import config
from app.context import request_id_var

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


def init_middlewares(app: FastAPI) -> None:
    app.middleware("http")(request_context_middleware)
