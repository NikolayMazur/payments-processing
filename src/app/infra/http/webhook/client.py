import hashlib
import hmac
from logging import getLogger

import httpx

from app.app_layer.interfaces.clients.webhook import AbstractWebhookClient, PaymentWebhookDTO, WebhookDeliveryError
from app.config import WebhookSettings

logger = getLogger(__name__)


class WebhookClient(AbstractWebhookClient):
    def __init__(self, client: httpx.AsyncClient, settings: WebhookSettings) -> None:
        self._client = client
        self._settings = settings

    async def send(self, url: str, data: PaymentWebhookDTO) -> None:
        body = data.model_dump_json().encode()
        try:
            response = await self._client.post(
                url,
                content=body,
                headers=self._build_headers(body),
                timeout=self._settings.TIMEOUT_SEC,
            )
        except httpx.HTTPError as exc:
            logger.warning("Webhook %s is unreachable: %r", url, exc)
            raise WebhookDeliveryError(f"Webhook {url} is unreachable") from exc
        if not response.is_success:
            logger.warning("Webhook %s responded with status %s", url, response.status_code)
            raise WebhookDeliveryError(f"Webhook {url} responded with status {response.status_code}")

    def _build_headers(self, body: bytes) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._settings.SECRET:
            # Подпись позволяет получателю убедиться, что уведомление пришло от нас и не изменено по дороге.
            signature = hmac.new(self._settings.SECRET.encode(), body, hashlib.sha256).hexdigest()
            headers[self._settings.SIGNATURE_HEADER] = f"sha256={signature}"
        return headers
