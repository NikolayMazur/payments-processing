from collections.abc import Mapping
from typing import Any

from app.config import RabbitSettings


class RetryService:
    """Считает попытки обработки сообщения и задержки между ними.

    В заголовке сообщения лежит число уже сделанных повторов. Первая доставка идёт без заголовка.
    """

    def __init__(self, settings: RabbitSettings) -> None:
        self._settings = settings

    def extract_retry_attempt(self, headers: Mapping[str, Any]) -> int:
        try:
            return max(int(headers.get(self._settings.RETRY_HEADER, 0)), 0)
        except (TypeError, ValueError):
            return 0

    def has_exhausted_attempts(self, retry_attempt: int) -> bool:
        return retry_attempt + 1 >= self._settings.MAX_ATTEMPTS

    def build_retry_headers(self, headers: Mapping[str, Any], retry_attempt: int) -> dict[str, Any]:
        return {**headers, self._settings.RETRY_HEADER: retry_attempt + 1}

    def get_delay_sec(self, retry_attempt: int) -> int:
        """Задержка перед повтором номер retry_attempt + 1: 2, 4, 8 секунд и так далее."""
        delay: int = self._settings.RETRY_BASE_DELAY_SEC * 2**retry_attempt
        return delay

    def get_retry_queue_name(self, retry_attempt: int) -> str:
        return f"{self._settings.QUEUE}.retry.{retry_attempt + 1}"

    @property
    def retries_qty(self) -> int:
        return self._settings.MAX_ATTEMPTS - 1
