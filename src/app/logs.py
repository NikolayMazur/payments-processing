import logging
import logging.config
from typing import Any

from app.config import Config
from app.context import get_request_id


class RequestIdFilter(logging.Filter):
    """Добавляет в запись лога идентификатор запроса, если он известен."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (request_id := get_request_id()) is not None:
            record.request_id = request_id
        return True


def get_logging_config(settings: Config) -> dict[str, Any]:
    formatter = "json" if settings.LOG_JSON else "plain"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
                "rename_fields": {"asctime": "time", "levelname": "level", "name": "logger"},
                "static_fields": {"service": settings.SERVICE_NAME},
            },
            "plain": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"},
        },
        "filters": {"request_id": {"()": "app.logs.RequestIdFilter"}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": formatter,
                "filters": ["request_id"],
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["default"], "level": settings.LOG_LEVEL},
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": settings.LOG_LEVEL, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": settings.LOG_LEVEL, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": settings.LOG_LEVEL, "propagate": False},
            # httpx на INFO пишет адрес каждого запроса целиком, а в адресе вебхука может лежать токен клиента.
            "httpx": {"level": "WARNING"},
        },
    }


def init_logging(settings: Config) -> None:
    logging.config.dictConfig(get_logging_config(settings))
