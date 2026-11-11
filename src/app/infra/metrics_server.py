from logging import getLogger

from prometheus_client import start_http_server

from app.config import MetricsSettings

logger = getLogger(__name__)


def start_metrics_server(settings: MetricsSettings) -> None:
    """Поднимает HTTP-сервер с метриками воркера.

    Занятый порт не причина не запускать воркер: без метрик он работать может, а без него платежи встанут.
    Так бывает, когда relay и консьюмер запущены на одной машине без контейнеров.
    """
    if not settings.ENABLED:
        return
    try:
        start_http_server(settings.PORT)
    except OSError:
        logger.warning("Metrics server is not started, port %s is unavailable", settings.PORT, exc_info=True)
