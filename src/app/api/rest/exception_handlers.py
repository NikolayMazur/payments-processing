from logging import getLogger

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.app_layer.interfaces.unit_of_work.exceptions import DatabaseUnavailableError
from app.errors import SERVICE_UNAVAILABLE_ERROR, UNKNOWN_ERROR

logger = getLogger(__name__)

RETRY_AFTER_SEC = 5


async def database_unavailable_handler(request: Request, exc: DatabaseUnavailableError) -> JSONResponse:
    logger.error("Database is unavailable on %s %s: %r", request.method, request.url.path, exc.__cause__)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": SERVICE_UNAVAILABLE_ERROR},
        headers={"Retry-After": str(RETRY_AFTER_SEC)},
    )


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Отдаёт ошибки валидации без самих входных данных.

    Стандартный ответ FastAPI возвращает клиенту то, что он прислал. Значение вроде NaN или одиночного
    суррогата в JSON ответа не сериализуется, и вместо 422 получался 500.
    """
    errors = [{"type": error["type"], "loc": error["loc"], "msg": error["msg"]} for error in exc.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": UNKNOWN_ERROR})


def init_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DatabaseUnavailableError, database_unavailable_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
