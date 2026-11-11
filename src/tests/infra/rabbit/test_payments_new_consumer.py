from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.rabbit.message import RabbitMessage
from pytest_mock import MockerFixture

from app.app_layer.interfaces.clients.webhook import WebhookDeliveryError
from app.app_layer.interfaces.locks.payment import PaymentLockedError
from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.app_layer.interfaces.services.payments.services import AbstractProcessPaymentService
from app.containers import Container
from app.context import get_request_id
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import payments_exchange, payments_new_queue
from app.infra.rabbit.workers import payments_new


class TestPaymentsNewConsumer:
    @pytest.fixture(autouse=True)
    async def _setup_mocks(self, container: Container, mocker: MockerFixture) -> AsyncIterator[None]:
        self.payment_id = uuid4()
        self.service: AsyncMock = mocker.AsyncMock(spec=AbstractProcessPaymentService)
        self.ack: MagicMock = mocker.patch.object(RabbitMessage, "ack", autospec=True)
        self.reject: MagicMock = mocker.patch.object(RabbitMessage, "reject", autospec=True)
        with container.process_payment_service.override(self.service):
            async with TestRabbitBroker(broker) as test_broker:
                self.broker: RabbitBroker = test_broker
                # Тестовый брокер не умеет TTL и dead-letter, поэтому публикацию в очередь задержки
                # перехватываем и проверяем её параметры. Настоящая цепочка проверяется в tests/integration.
                self.retry_publish: AsyncMock = mocker.patch.object(payments_new, "broker").publish
                self.retry_publish.side_effect = AsyncMock()
                yield

    async def _publish(self, body: Any = None, headers: dict[str, Any] | None = None) -> None:
        await self.broker.publish(
            body if body is not None else {"payment_id": str(self.payment_id)},
            exchange=payments_exchange,
            routing_key=payments_new_queue.routing_key,
            headers=headers,
            message_id="outbox-record-id",
        )

    async def test_processed_message_acked(self) -> None:
        await self._publish()

        self.service.process.assert_awaited_once_with(ProcessPaymentInputDTO(payment_id=self.payment_id))
        self.ack.assert_called_once()
        self.reject.assert_not_called()
        self.retry_publish.assert_not_called()

    @pytest.mark.parametrize(
        ("headers", "expected_queue", "expected_attempt"),
        [
            (None, "payments.new.retry.1", 1),
            ({"x-retry-attempt": 1}, "payments.new.retry.2", 2),
        ],
    )
    async def test_failed_message_goes_to_delay_queue(
        self,
        headers: dict[str, Any] | None,
        expected_queue: str,
        expected_attempt: int,
    ) -> None:
        self.service.process.side_effect = WebhookDeliveryError

        await self._publish(headers=(headers or {}) | {"x-request-id": "req-42"})

        self.retry_publish.assert_called_once()
        call = self.retry_publish.call_args
        assert call.args == ({"payment_id": str(self.payment_id)},)
        assert call.kwargs["exchange"].name == "payments.retry"
        assert call.kwargs["routing_key"] == expected_queue
        assert call.kwargs["headers"]["x-retry-attempt"] == expected_attempt
        assert call.kwargs["headers"]["x-request-id"] == "req-42"
        assert call.kwargs["message_id"] == "outbox-record-id"
        assert call.kwargs["persist"] is True
        # Исходное сообщение подтверждено, дальше живёт только его копия в очереди задержки.
        self.ack.assert_called_once()
        self.reject.assert_not_called()

    async def test_message_stays_unacked_when_retry_publish_fails(self) -> None:
        self.service.process.side_effect = WebhookDeliveryError
        self.retry_publish.side_effect = ConnectionError("broker is down")

        with pytest.raises(ConnectionError):
            await self._publish()

        self.ack.assert_not_called()
        self.reject.assert_not_called()

    async def test_copy_of_message_in_progress_is_dropped(self) -> None:
        self.service.process.side_effect = PaymentLockedError

        await self._publish()

        # Не повтор и не DLQ: платёж доведёт до конца та копия, которая его сейчас держит.
        self.ack.assert_called_once()
        self.reject.assert_not_called()
        self.retry_publish.assert_not_called()

    async def test_third_failure_rejected_to_dlq(self) -> None:
        self.service.process.side_effect = WebhookDeliveryError

        await self._publish(headers={"x-retry-attempt": 2})

        self.reject.assert_called_once()
        assert self.reject.call_args.kwargs == {"requeue": False}
        self.ack.assert_not_called()
        self.retry_publish.assert_not_called()

    async def test_unknown_payment_rejected_without_retry(self) -> None:
        self.service.process.side_effect = PaymentNotFoundError

        await self._publish()

        self.reject.assert_called_once()
        assert self.reject.call_args.kwargs == {"requeue": False}
        self.retry_publish.assert_not_called()

    @pytest.mark.parametrize("body", [{"payment_id": "not-a-uuid"}, {"foo": "bar"}, "just a string"])
    async def test_invalid_message_rejected_without_processing(self, body: Any) -> None:
        await self._publish(body=body)

        self.service.process.assert_not_awaited()
        self.reject.assert_called_once()
        self.retry_publish.assert_not_called()

    async def test_request_id_from_message_is_available_while_processing(self) -> None:
        seen: list[str | None] = []
        self.service.process.side_effect = lambda _: seen.append(get_request_id())

        await self._publish(headers={"x-request-id": "req-42"})

        assert seen == ["req-42"]
        assert get_request_id() is None
