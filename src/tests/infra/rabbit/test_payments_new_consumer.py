from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.rabbit.annotations import RabbitMessage as RabbitMessageContext
from faststream.rabbit.message import RabbitMessage
from pytest_mock import MockerFixture

from app.app_layer.interfaces.clients.webhook import WebhookDeliveryError
from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.app_layer.interfaces.services.payments.services import AbstractProcessPaymentService
from app.containers import Container
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import payments_exchange, payments_new_queue, retry_exchange, retry_queues
from app.infra.rabbit.workers import payments_new  # noqa: F401  регистрирует подписчика на брокере

retried: list[tuple[str, Any, dict[str, Any]]] = []


def _subscribe_retry_queue(queue_name: str) -> None:
    # Тестовый брокер не умеет TTL и dead-letter, поэтому очереди задержки слушаем сами
    # и проверяем, что и с какими заголовками в них пришло.
    queue = next(queue for queue in retry_queues if queue.name == queue_name)

    @broker.subscriber(queue, retry_exchange)
    async def retry_queue_listener(body: Any, message: RabbitMessageContext) -> None:
        retried.append((queue_name, body, dict(message.headers)))


for retry_queue in retry_queues:
    _subscribe_retry_queue(retry_queue.name)


class TestPaymentsNewConsumer:
    @pytest.fixture(autouse=True)
    async def _setup_mocks(self, container: Container, mocker: MockerFixture) -> AsyncIterator[None]:
        retried.clear()
        self.payment_id = uuid4()
        self.service: AsyncMock = mocker.AsyncMock(spec=AbstractProcessPaymentService)
        self.ack: MagicMock = mocker.patch.object(RabbitMessage, "ack", autospec=True)
        self.reject: MagicMock = mocker.patch.object(RabbitMessage, "reject", autospec=True)
        with container.process_payment_service.override(self.service):
            async with TestRabbitBroker(broker) as test_broker:
                self.broker: RabbitBroker = test_broker
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
        assert retried == []

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

        await self._publish(headers=headers)

        assert len(retried) == 1
        queue_name, body, retry_headers = retried[0]
        assert queue_name == expected_queue
        assert body == {"payment_id": str(self.payment_id)}
        assert retry_headers["x-retry-attempt"] == expected_attempt
        # Исходное сообщение подтверждено, дальше живёт только его копия в очереди задержки.
        assert self.ack.call_count == 2
        self.reject.assert_not_called()

    async def test_third_failure_rejected_to_dlq(self) -> None:
        self.service.process.side_effect = WebhookDeliveryError

        await self._publish(headers={"x-retry-attempt": 2})

        self.reject.assert_called_once()
        assert self.reject.call_args.kwargs == {"requeue": False}
        self.ack.assert_not_called()
        assert retried == []

    async def test_unknown_payment_rejected_without_retry(self) -> None:
        self.service.process.side_effect = PaymentNotFoundError

        await self._publish()

        self.reject.assert_called_once()
        assert self.reject.call_args.kwargs == {"requeue": False}
        assert retried == []

    @pytest.mark.parametrize("body", [{"payment_id": "not-a-uuid"}, {"foo": "bar"}, "just a string"])
    async def test_invalid_message_rejected_without_processing(self, body: Any) -> None:
        await self._publish(body=body)

        self.service.process.assert_not_awaited()
        self.reject.assert_called_once()
        assert retried == []
