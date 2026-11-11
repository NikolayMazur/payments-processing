import hashlib
import hmac
import json
from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.app_layer.interfaces.clients.webhook import PaymentWebhookDTO, WebhookDeliveryError
from app.config import WebhookSettings
from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum
from app.infra.http.webhook.client import WebhookClient
from app.utils import utc_now
from tests.constants import TEST_WEBHOOK_URL

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture()
def webhook() -> PaymentWebhookDTO:
    now = utc_now()
    return PaymentWebhookDTO(
        payment_id=uuid4(),
        status=PaymentStatusEnum.SUCCEEDED,
        amount=Decimal("100.50"),
        currency=CurrencyEnum.RUB,
        description="Заказ 42",
        metadata={"order_id": 42},
        failure_reason=None,
        created_at=now,
        processed_at=now,
    )


def build_client(handler: Handler, secret: str = "") -> WebhookClient:
    return WebhookClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        settings=WebhookSettings(SECRET=secret),
    )


async def test_webhook_sent_as_signed_json(webhook: PaymentWebhookDTO) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    await build_client(handler, secret="webhook-secret").send(url=TEST_WEBHOOK_URL, data=webhook)

    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == TEST_WEBHOOK_URL
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["payment_id"] == str(webhook.payment_id)
    assert body["status"] == "succeeded"
    assert body["amount"] == "100.50"
    expected_signature = hmac.new(b"webhook-secret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-Signature"] == f"sha256={expected_signature}"


async def test_webhook_without_secret_not_signed(webhook: PaymentWebhookDTO) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    await build_client(handler).send(url=TEST_WEBHOOK_URL, data=webhook)

    assert "X-Signature" not in requests[0].headers


@pytest.mark.parametrize("status_code", [301, 400, 404, 500, 503])
async def test_not_success_response_is_delivery_error(webhook: PaymentWebhookDTO, status_code: int) -> None:
    client = build_client(lambda _: httpx.Response(status_code))

    with pytest.raises(WebhookDeliveryError):
        await client.send(url=TEST_WEBHOOK_URL, data=webhook)


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("timeout")])
async def test_network_error_is_delivery_error(webhook: PaymentWebhookDTO, error: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(WebhookDeliveryError):
        await build_client(handler).send(url=TEST_WEBHOOK_URL, data=webhook)
