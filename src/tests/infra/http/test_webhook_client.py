import base64
import hashlib
import hmac
import json
import time
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

RAW_KEY = b"0123456789abcdef0123456789abcdef"
STANDARD_SECRET = "whsec_" + base64.b64encode(RAW_KEY).decode()


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


def expected_signature(key: bytes, request: httpx.Request) -> str:
    """Подпись, как её посчитает получатель по Standard Webhooks: HMAC-SHA256 от «id.timestamp.тело»."""
    signed = f"{request.headers['webhook-id']}.{request.headers['webhook-timestamp']}.".encode() + request.content
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


@pytest.mark.parametrize(
    ("secret", "key"),
    [(STANDARD_SECRET, RAW_KEY), ("plain-secret", b"plain-secret")],
)
async def test_webhook_sent_with_standard_headers(webhook: PaymentWebhookDTO, secret: str, key: bytes) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    await build_client(handler, secret=secret).send(url=TEST_WEBHOOK_URL, data=webhook)

    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == TEST_WEBHOOK_URL
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["payment_id"] == str(webhook.payment_id)
    assert body["status"] == "succeeded"
    assert body["amount"] == "100.50"
    assert request.headers["webhook-id"] == str(webhook.payment_id)
    assert abs(int(request.headers["webhook-timestamp"]) - time.time()) < 5
    assert request.headers["webhook-signature"] == expected_signature(key, request)


async def test_webhook_id_is_stable_between_retries(webhook: PaymentWebhookDTO) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    client = build_client(handler, secret=STANDARD_SECRET)
    await client.send(url=TEST_WEBHOOK_URL, data=webhook)
    await client.send(url=TEST_WEBHOOK_URL, data=webhook)

    assert requests[0].headers["webhook-id"] == requests[1].headers["webhook-id"]


async def test_webhook_without_secret_not_signed(webhook: PaymentWebhookDTO) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    await build_client(handler).send(url=TEST_WEBHOOK_URL, data=webhook)

    assert "webhook-signature" not in requests[0].headers
    assert "webhook-id" in requests[0].headers
    assert "webhook-timestamp" in requests[0].headers


def test_broken_standard_secret_fails_on_start() -> None:
    with pytest.raises(ValueError, match="base64|Base64|padding|Padding"):
        build_client(lambda _: httpx.Response(200), secret="whsec_not-base64!")


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


async def test_url_secrets_are_not_logged(webhook: PaymentWebhookDTO, caplog: pytest.LogCaptureFixture) -> None:
    url = "https://user:password@client.example.com/hooks/payments?token=very-secret"
    client = build_client(lambda _: httpx.Response(500))

    with pytest.raises(WebhookDeliveryError) as exc_info:
        await client.send(url=url, data=webhook)

    logged = caplog.text + str(exc_info.value)
    assert "https://client.example.com/hooks/payments" in logged
    assert "very-secret" not in logged
    assert "password" not in logged
