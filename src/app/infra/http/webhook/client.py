import asyncio
import base64
import hashlib
import hmac
from logging import getLogger

import httpx

from app.app_layer.interfaces.clients.webhook import AbstractWebhookClient, PaymentWebhookDTO, WebhookDeliveryError
from app.config import WebhookSettings
from app.utils import mask_url, utc_now

logger = getLogger(__name__)

WEBHOOK_ID_HEADER = "webhook-id"
WEBHOOK_TIMESTAMP_HEADER = "webhook-timestamp"
WEBHOOK_SIGNATURE_HEADER = "webhook-signature"
SECRET_PREFIX = "whsec_"  # noqa: S105  # nosec B105  это префикс формата, а не секрет
SIGNATURE_VERSION = "v1"


class WebhookClient(AbstractWebhookClient):
    """Отправляет уведомления по соглашению Standard Webhooks.

    Заголовок webhook-id не меняется между повторами, по нему получатель отсекает дубли.
    Подписывается строка «id.timestamp.тело», поэтому перехваченный запрос нельзя переслать
    позже: получатель сверяет webhook-timestamp с текущим временем.
    """

    def __init__(self, client: httpx.AsyncClient, settings: WebhookSettings) -> None:
        self._client = client
        self._settings = settings
        self._signing_key = self._parse_secret(settings.SECRET)

    async def send(self, url: str, data: PaymentWebhookDTO) -> None:
        body = data.model_dump_json().encode()
        # У платежа одно событие, поэтому его id однозначно определяет уведомление.
        headers = self._build_headers(webhook_id=str(data.payment_id), body=body)
        try:
            # Таймауты httpx действуют на каждую операцию чтения отдельно: получатель, отдающий ответ по байту,
            # держал бы обработчик сколько угодно. Поэтому поверх стоит предел на весь запрос.
            async with asyncio.timeout(self._settings.TIMEOUT_SEC):
                status_code = await self._post(url, body, headers)
        except (httpx.HTTPError, TimeoutError) as exc:
            logger.warning("Webhook %s is unreachable: %s", mask_url(url), type(exc).__name__)
            raise WebhookDeliveryError(f"Webhook {mask_url(url)} is unreachable") from exc
        if not httpx.codes.is_success(status_code):
            logger.warning("Webhook %s responded with status %s", mask_url(url), status_code)
            raise WebhookDeliveryError(f"Webhook {mask_url(url)} responded with status {status_code}")

    async def _post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        # Нужен только код ответа. Тело не читаем: получатель может прислать сколько угодно данных.
        request_timeout = self._settings.TIMEOUT_SEC
        async with self._client.stream("POST", url, content=body, headers=headers, timeout=request_timeout) as response:
            return response.status_code

    def _build_headers(self, webhook_id: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(utc_now().timestamp()))
        headers = {
            "Content-Type": "application/json",
            WEBHOOK_ID_HEADER: webhook_id,
            WEBHOOK_TIMESTAMP_HEADER: timestamp,
        }
        if self._signing_key:
            signed_content = f"{webhook_id}.{timestamp}.".encode() + body
            signature = hmac.new(self._signing_key, signed_content, hashlib.sha256).digest()
            headers[WEBHOOK_SIGNATURE_HEADER] = f"{SIGNATURE_VERSION},{base64.b64encode(signature).decode()}"
        return headers

    @staticmethod
    def _parse_secret(secret: str) -> bytes:
        if secret.startswith(SECRET_PREFIX):
            return base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
        return secret.encode()
