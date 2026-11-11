import logging
import logging.config
from typing import Any

from app.config import Config


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
        "handlers": {
            "default": {"class": "logging.StreamHandler", "formatter": formatter, "stream": "ext://sys.stdout"},
        },
        "root": {"handlers": ["default"], "level": settings.LOG_LEVEL},
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": settings.LOG_LEVEL, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": settings.LOG_LEVEL, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": settings.LOG_LEVEL, "propagate": False},
        },
    }


def init_logging(settings: Config) -> None:
    logging.config.dictConfig(get_logging_config(settings))
