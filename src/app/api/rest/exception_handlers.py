from logging import getLogger

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.errors import UNKNOWN_ERROR

logger = getLogger(__name__)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": UNKNOWN_ERROR})


def init_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, unhandled_exception_handler)
