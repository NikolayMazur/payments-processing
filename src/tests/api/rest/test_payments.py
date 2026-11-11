import asyncio
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

from app.api.rest.main import app
from app.config import config
from app.containers import Container
from app.domain.outbox.enums import OutboxStatusEnum
from app.domain.payments.enums import PaymentStatusEnum
from app.errors import ErrorCodeEnum
from app.infra.repositories.sqla.payment import PaymentRepository
from app.logs import RequestIdFilter
from tests.constants import TEST_API_KEY, TEST_WEBHOOK_URL
from tests.environment.unit_of_work import TestUow
from tests.factories import PaymentFactory

PAYMENTS_URL = "/api/v1/payments"


def build_body(**kwargs: Any) -> dict[str, Any]:
    body = {
        "amount": "1500.00",
        "currency": "RUB",
        "description": "Заказ 42",
        "metadata": {"order_id": 42},
        "webhook_url": TEST_WEBHOOK_URL,
    }
    return body | kwargs


class TestCreatePayment:
    async def test_create_payment_ok(self, client: AsyncClient, uow: TestUow) -> None:
        response = await client.post(PAYMENTS_URL, json=build_body(), headers={"Idempotency-Key": "key-1"})

        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert set(data) == {"payment_id", "status", "created_at"}
        assert data["status"] == PaymentStatusEnum.PENDING.value

        async with uow:
            payment = await uow.payment.get_by_id(UUID(data["payment_id"]))
            outbox = await uow.get_all_outbox()
        assert payment is not None
        assert payment.amount == Decimal("1500.00")
        assert payment.metadata == {"order_id": 42}
        assert payment.idempotency_key == "key-1"
        assert payment.processed_at is None

        assert len(outbox) == 1
        assert outbox[0].status is OutboxStatusEnum.PENDING
        assert outbox[0].exchange == config.RABBIT.EXCHANGE
        assert outbox[0].routing_key == config.RABBIT.QUEUE
        assert outbox[0].payload == {"payment_id": data["payment_id"]}

    async def test_create_payment_only_required_fields_ok(self, client: AsyncClient) -> None:
        body = {"amount": 10, "currency": "USD", "webhook_url": TEST_WEBHOOK_URL}

        response = await client.post(PAYMENTS_URL, json=body, headers={"Idempotency-Key": "key-1"})

        assert response.status_code == status.HTTP_202_ACCEPTED

    async def test_repeat_with_same_key_returns_same_payment(self, client: AsyncClient, uow: TestUow) -> None:
        headers = {"Idempotency-Key": "key-1"}
        first = await client.post(PAYMENTS_URL, json=build_body(), headers=headers)

        # Сумма записана иначе, но это тот же платёж: 1500.00 == 1500.0
        second = await client.post(PAYMENTS_URL, json=build_body(amount="1500.0"), headers=headers)

        assert second.status_code == status.HTTP_202_ACCEPTED
        assert second.json() == first.json()
        async with uow:
            assert await uow.count_payments() == 1
            assert len(await uow.get_all_outbox()) == 1

    async def test_same_key_with_other_body_conflict(self, client: AsyncClient, uow: TestUow) -> None:
        headers = {"Idempotency-Key": "key-1"}
        await client.post(PAYMENTS_URL, json=build_body(), headers=headers)

        response = await client.post(PAYMENTS_URL, json=build_body(amount="999.99"), headers=headers)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"]["message_code"] == ErrorCodeEnum.IDEMPOTENCY_KEY_CONFLICT_ERROR_CODE.value
        async with uow:
            assert await uow.count_payments() == 1
            assert len(await uow.get_all_outbox()) == 1

    async def test_concurrent_requests_with_same_key_create_one_payment(
        self,
        client: AsyncClient,
        uow: TestUow,
    ) -> None:
        headers = {"Idempotency-Key": "key-1"}

        responses = await asyncio.gather(
            *(client.post(PAYMENTS_URL, json=build_body(), headers=headers) for _ in range(5)),
        )

        assert {response.status_code for response in responses} == {status.HTTP_202_ACCEPTED}
        assert len({response.json()["payment_id"] for response in responses}) == 1
        async with uow:
            assert await uow.count_payments() == 1
            assert len(await uow.get_all_outbox()) == 1

    async def test_broken_json_bad_request(self, client: AsyncClient) -> None:
        headers = {"Idempotency-Key": "key-1", "Content-Type": "application/json"}

        response = await client.post(PAYMENTS_URL, content=b"\xff\xfe not json", headers=headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_different_keys_create_different_payments(self, client: AsyncClient, uow: TestUow) -> None:
        first = await client.post(PAYMENTS_URL, json=build_body(), headers={"Idempotency-Key": "key-1"})
        second = await client.post(PAYMENTS_URL, json=build_body(), headers={"Idempotency-Key": "key-2"})

        assert first.json()["payment_id"] != second.json()["payment_id"]
        async with uow:
            assert await uow.count_payments() == 2

    async def test_without_idempotency_key_unprocessable(self, client: AsyncClient, uow: TestUow) -> None:
        response = await client.post(PAYMENTS_URL, json=build_body())

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        async with uow:
            assert await uow.count_payments() == 0

    @pytest.mark.parametrize(
        "invalid",
        [
            {"amount": "0"},
            {"amount": "-5"},
            {"amount": "10.001"},
            {"currency": "GBP"},
            {"webhook_url": "not-a-url"},
            {"metadata": "not-a-dict"},
            {"description": "x" * 1025},
            {"metadata": {"blob": "x" * 17_000}},
            # Нулевой символ PostgreSQL не хранит, без проверки эти запросы падали с 500.
            {"description": "a\x00b"},
            {"metadata": {"key": "a\x00b"}},
            {"metadata": {"a\x00b": 1}},
            {"metadata": {"nested": [{"deep": "a\x00b"}]}},
            {"amount": "NaN"},
            {"amount": "Infinity"},
        ],
    )
    async def test_invalid_body_unprocessable(self, client: AsyncClient, invalid: dict[str, Any]) -> None:
        response = await client.post(PAYMENTS_URL, json=build_body(**invalid), headers={"Idempotency-Key": "key-1"})

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestCornerCases:
    """Запросы, на которых сервис раньше отвечал 500. Тело задаётся сырым JSON: часть значений иначе не передать."""

    @staticmethod
    def raw_body(**replacements: str) -> bytes:
        fields = {
            "amount": '"10.00"',
            "currency": '"RUB"',
            "webhook_url": f'"{TEST_WEBHOOK_URL}"',
            "description": '""',
            "metadata": "{}",
        }
        return ("{" + ",".join(f'"{key}":{value}' for key, value in (fields | replacements).items()) + "}").encode()

    @pytest.mark.parametrize(
        "replacements",
        [
            # Python разбирает эти литералы, а PostgreSQL в jsonb не принимает.
            {"metadata": '{"a":NaN}'},
            {"metadata": '{"a":Infinity}'},
            {"metadata": '{"a":[-Infinity]}'},
            {"metadata": '{"a":1e400}'},
            # Одиночный суррогат: в UTF-8 не кодируется.
            {"description": '"\\ud800"'},
            {"metadata": '{"k":"\\udfff"}'},
            {"metadata": '{"\\ud800":1}'},
            {"metadata": '{"a":' + "[" * 33 + "]" * 33 + "}"},
            {"metadata": '{"a":' + "[" * 900 + "]" * 900 + "}"},
            {"metadata": '{"a":' + "[" * 5000 + "]" * 5000 + "}"},
            {"amount": "NaN"},
            {"amount": "Infinity"},
            {"amount": '"+5"'},
            {"amount": '" 5"'},
            {"amount": '"1_000"'},
            {"amount": '"١٢٣"'},
            {"amount": '"1E-2"'},
        ],
        ids=lambda value: str(value)[:40],
    )
    async def test_unstorable_input_is_client_error(
        self,
        client: AsyncClient,
        uow: TestUow,
        replacements: dict[str, str],
    ) -> None:
        headers = {"Idempotency-Key": "key-1", "Content-Type": "application/json"}

        response = await client.post(PAYMENTS_URL, content=self.raw_body(**replacements), headers=headers)

        assert response.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_CONTENT}
        async with uow:
            assert await uow.count_payments() == 0

    async def test_validation_error_does_not_echo_input(self, client: AsyncClient) -> None:
        response = await client.post(
            PAYMENTS_URL,
            json=build_body(description="secret-value-" + "x" * 1100),
            headers={"Idempotency-Key": "key-1"},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert "secret-value" not in response.text
        assert set(response.json()["detail"][0]) == {"type", "loc", "msg"}

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ('{"a":1}', '{"a":1.0}'),
            ('{"a":[1,2],"b":{"c":3}}', '{"b":{"c":3.0},"a":[1.0,2.0]}'),
        ],
    )
    async def test_same_number_in_other_notation_is_same_request(
        self,
        client: AsyncClient,
        first: str,
        second: str,
    ) -> None:
        headers = {"Idempotency-Key": "key-1", "Content-Type": "application/json"}
        created = await client.post(PAYMENTS_URL, content=self.raw_body(metadata=first), headers=headers)

        repeated = await client.post(PAYMENTS_URL, content=self.raw_body(metadata=second), headers=headers)

        assert repeated.status_code == status.HTTP_202_ACCEPTED
        assert repeated.json() == created.json()

    async def test_really_different_number_is_conflict(self, client: AsyncClient) -> None:
        headers = {"Idempotency-Key": "key-1", "Content-Type": "application/json"}
        await client.post(PAYMENTS_URL, content=self.raw_body(metadata='{"a":1}'), headers=headers)

        response = await client.post(PAYMENTS_URL, content=self.raw_body(metadata='{"a":1.5}'), headers=headers)

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_too_large_body_rejected(self, client: AsyncClient, uow: TestUow) -> None:
        body = build_body(description="x" * (config.API.MAX_BODY_BYTES + 1))

        response = await client.post(PAYMENTS_URL, json=body, headers={"Idempotency-Key": "key-1"})

        assert response.status_code == status.HTTP_413_CONTENT_TOO_LARGE
        assert response.json()["detail"]["message_code"] == ErrorCodeEnum.PAYLOAD_TOO_LARGE_ERROR_CODE.value
        async with uow:
            assert await uow.count_payments() == 0

    async def test_too_large_body_without_content_length_rejected(self, client: AsyncClient) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            # Тело отдаётся потоком, заголовка Content-Length нет: предел проверяется по факту.
            yield b'{"amount":"1","currency":"RUB","webhook_url":"https://example.com/h","description":"'
            for _ in range(config.API.MAX_BODY_BYTES // 1024 + 2):
                yield b"x" * 1024
            yield b'"}'

        response = await client.post(
            PAYMENTS_URL,
            content=chunks(),
            headers={"Idempotency-Key": "key-1", "Content-Type": "application/json"},
        )

        assert response.status_code == status.HTTP_413_CONTENT_TOO_LARGE

    async def test_body_at_reasonable_maximum_accepted(self, client: AsyncClient) -> None:
        body = build_body(description="я" * 1024, metadata={"k": "x" * (config.API.MAX_METADATA_BYTES - 8)})

        response = await client.post(PAYMENTS_URL, json=body, headers={"Idempotency-Key": "key-1"})

        assert response.status_code == status.HTTP_202_ACCEPTED

    async def test_two_idempotency_keys_in_one_request_rejected(self, client: AsyncClient, uow: TestUow) -> None:
        headers = [
            ("Idempotency-Key", "key-1"),
            ("Idempotency-Key", "key-2"),
            ("X-API-Key", TEST_API_KEY),
            ("Content-Type", "application/json"),
        ]

        response = await client.post(PAYMENTS_URL, content=self.raw_body(), headers=headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"]["message_code"] == ErrorCodeEnum.DUPLICATE_IDEMPOTENCY_KEY_ERROR_CODE.value
        async with uow:
            assert await uow.count_payments() == 0


class TestRequestId:
    async def test_client_request_id_is_echoed_and_saved_to_event(self, client: AsyncClient, uow: TestUow) -> None:
        headers = {"Idempotency-Key": "key-1", "X-Request-ID": "req-42"}

        response = await client.post(PAYMENTS_URL, json=build_body(), headers=headers)

        assert response.headers["X-Request-ID"] == "req-42"
        async with uow:
            outbox = await uow.get_all_outbox()
        assert outbox[0].headers == {"x-request-id": "req-42"}

    async def test_request_id_is_generated_when_absent(self, client: AsyncClient, uow: TestUow) -> None:
        response = await client.post(PAYMENTS_URL, json=build_body(), headers={"Idempotency-Key": "key-1"})

        request_id = response.headers["X-Request-ID"]
        assert UUID(request_id)
        async with uow:
            outbox = await uow.get_all_outbox()
        assert outbox[0].headers == {"x-request-id": request_id}

    async def test_request_id_added_to_logs(self, client: AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
        headers = {"Idempotency-Key": "key-1", "X-Request-ID": "req-42"}
        await client.post(PAYMENTS_URL, json=build_body(), headers=headers)
        caplog.clear()
        caplog.handler.addFilter(RequestIdFilter())

        with caplog.at_level(logging.INFO):
            await client.post(PAYMENTS_URL, json=build_body(), headers=headers)

        records = [record for record in caplog.records if "returned by idempotency key" in record.getMessage()]
        assert [getattr(record, "request_id", None) for record in records] == ["req-42"]


class TestMetrics:
    async def test_metrics_exposed_without_api_key(self, client: AsyncClient) -> None:
        await client.post(PAYMENTS_URL, json=build_body(), headers={"Idempotency-Key": "key-1"})
        await client.get(f"{PAYMENTS_URL}/{uuid4()}")
        client.headers.pop("X-API-Key")

        response = await client.get("/metrics")

        assert response.status_code == status.HTTP_200_OK
        assert 'payments_created_total{currency="RUB"}' in response.text
        # В метке шаблон маршрута, а не конкретный идентификатор платежа.
        assert 'http_requests_total{method="GET",path="/api/v1/payments/{payment_id}",status="404"}' in response.text
        assert 'path="/metrics"' not in response.text


class TestGetPayment:
    async def test_get_payment_ok(self, client: AsyncClient, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()

        response = await client.get(f"{PAYMENTS_URL}/{payment.id}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "payment_id": str(payment.id),
            "amount": "100.50",
            "currency": "RUB",
            "description": payment.description,
            "metadata": payment.metadata,
            "status": "pending",
            "idempotency_key": payment.idempotency_key,
            "webhook_url": payment.webhook_url,
            "failure_reason": None,
            "created_at": payment.created_at.isoformat().replace("+00:00", "Z"),
            "processed_at": None,
            "webhook_attempts": 0,
            "webhook_delivered_at": None,
        }

    async def test_get_unknown_payment_not_found(self, client: AsyncClient) -> None:
        response = await client.get(f"{PAYMENTS_URL}/{uuid4()}")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"]["message_code"] == ErrorCodeEnum.PAYMENT_NOT_FOUND_ERROR_CODE.value


class TestAuth:
    @pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong-key"}, {"X-API-Key": ""}])
    async def test_invalid_api_key_unauthorized(self, client: AsyncClient, headers: dict[str, str]) -> None:
        client.headers.pop("X-API-Key")

        create_response = await client.post(
            PAYMENTS_URL,
            json=build_body(),
            headers={"Idempotency-Key": "key-1"} | headers,
        )
        get_response = await client.get(f"{PAYMENTS_URL}/{uuid4()}", headers=headers)

        assert create_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert get_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert create_response.json()["detail"]["message_code"] == ErrorCodeEnum.UNAUTHORIZED_ERROR_CODE.value

    async def test_api_key_not_configured_unauthorized(self, client: AsyncClient, mocker: MockerFixture) -> None:
        mocker.patch.object(config.AUTH, "API_KEY", "")

        response = await client.get(f"{PAYMENTS_URL}/{uuid4()}", headers={"X-API-Key": ""})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestDatabaseUnavailable:
    @pytest.mark.parametrize(
        "error",
        [
            OperationalError("INSERT", {}, Exception("the database system is shutting down")),
            InterfaceError("INSERT", {}, Exception("connection is closed")),
            ConnectionRefusedError("connect call failed"),
            TimeoutError(),
        ],
    )
    async def test_lost_connection_service_unavailable(
        self,
        client: AsyncClient,
        mocker: MockerFixture,
        error: Exception,
    ) -> None:
        mocker.patch.object(PaymentRepository, "create_if_absent", side_effect=error)
        mocker.patch.object(PaymentRepository, "get_by_id", side_effect=error)

        create_response = await client.post(PAYMENTS_URL, json=build_body(), headers={"Idempotency-Key": "key-1"})
        get_response = await client.get(f"{PAYMENTS_URL}/{uuid4()}")

        for response in (create_response, get_response):
            assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            assert response.json()["detail"]["message_code"] == ErrorCodeEnum.SERVICE_UNAVAILABLE_ERROR_CODE.value
            assert response.headers["Retry-After"] == "5"

    async def test_request_can_be_repeated_after_database_is_back(
        self,
        client: AsyncClient,
        uow: TestUow,
        mocker: MockerFixture,
    ) -> None:
        headers = {"Idempotency-Key": "key-1"}
        failure = mocker.patch.object(PaymentRepository, "create_if_absent", side_effect=ConnectionRefusedError)
        assert (await client.post(PAYMENTS_URL, json=build_body(), headers=headers)).status_code == 503
        mocker.stop(failure)

        response = await client.post(PAYMENTS_URL, json=build_body(), headers=headers)

        assert response.status_code == status.HTTP_202_ACCEPTED
        async with uow:
            assert await uow.count_payments() == 1

    async def test_query_error_is_not_masked_as_unavailable(self, container: Container, mocker: MockerFixture) -> None:
        error = ProgrammingError("INSERT", {}, Exception("column does not exist"))
        mocker.patch.object(PaymentRepository, "create_if_absent", side_effect=error)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        headers = {"X-API-Key": TEST_API_KEY, "Idempotency-Key": "key-1"}

        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            response = await client.post(PAYMENTS_URL, json=build_body())

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
