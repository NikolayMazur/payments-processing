"""Цепочка повторов на настоящем RabbitMQ: очереди задержки, возврат по TTL, DLQ и возврат из неё."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from faststream.rabbit import RabbitBroker
from pytest_mock import MockerFixture

from app.app_layer.interfaces.clients.webhook import WebhookDeliveryError
from app.app_layer.interfaces.publishers.events import EventPublishError
from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.services import AbstractProcessPaymentService
from app.config import config
from app.containers import Container
from app.domain.outbox.entity import Outbox
from app.infra.rabbit.dead_letters import RabbitDeadLetterQueue
from app.infra.rabbit.publisher import RabbitEventPublisher
from app.infra.rabbit.topology import dead_letter_queue, payments_exchange, payments_new_queue
from app.utils import utc_now

pytestmark = pytest.mark.integration

WAIT_TIMEOUT_SEC = 20


async def wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while not condition():
        assert time.monotonic() < deadline, "condition is not met in time"
        await asyncio.sleep(0.05)


class TestRetryAndDlq:
    @pytest.fixture(autouse=True)
    async def _setup(
        self,
        rabbit_broker: RabbitBroker,
        container: Container,
        mocker: MockerFixture,
    ) -> AsyncIterator[None]:
        self.broker = rabbit_broker
        self.publisher = RabbitEventPublisher(broker=rabbit_broker)
        self.dead_letters = RabbitDeadLetterQueue(broker=rabbit_broker, settings=config.RABBIT)
        self.calls: list[tuple[UUID, float]] = []
        self.outcomes: list[Exception | None] = []
        self.service: AsyncMock = mocker.AsyncMock(spec=AbstractProcessPaymentService)
        self.service.process.side_effect = self._process
        with container.process_payment_service.override(self.service):
            yield
            await self._purge()

    async def _process(self, data: ProcessPaymentInputDTO) -> None:
        self.calls.append((data.payment_id, time.monotonic()))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome

    async def _purge(self) -> None:
        for queue in (dead_letter_queue, payments_new_queue):
            await (await self.broker.declare_queue(queue)).purge()

    async def _publish(self, payment_id: UUID, routing_key: str = config.RABBIT.QUEUE) -> None:
        await self.publisher.publish(
            Outbox(
                exchange=config.RABBIT.EXCHANGE,
                routing_key=routing_key,
                event_key=str(payment_id),
                payload={"payment_id": str(payment_id)},
                headers={"x-request-id": "req-42"},
                created_at=utc_now(),
            ),
        )

    async def _wait_dlq(self, count: int) -> None:
        deadline = time.monotonic() + WAIT_TIMEOUT_SEC
        while await self.dead_letters.count() != count:
            assert time.monotonic() < deadline, f"DLQ has {await self.dead_letters.count()} messages, expected {count}"
            await asyncio.sleep(0.2)

    async def test_three_failed_attempts_with_growing_delay_then_dlq(self) -> None:
        payment_id = uuid4()
        self.outcomes = [WebhookDeliveryError(), WebhookDeliveryError(), WebhookDeliveryError()]

        await self._publish(payment_id)
        await self._wait_dlq(1)

        assert [call[0] for call in self.calls] == [payment_id] * 3
        first_gap, second_gap = (self.calls[i + 1][1] - self.calls[i][1] for i in range(2))
        # Задержки задаёт TTL очередей payments.new.retry.1 и .2: 2 и 4 секунды.
        assert 1.9 <= first_gap < 3.5
        assert 3.9 <= second_gap < 5.5

        message = await (await self.broker.declare_queue(dead_letter_queue)).get(timeout=5)
        assert message is not None
        assert message.body == f'{{"payment_id":"{payment_id}"}}'.encode()
        assert message.headers["x-retry-attempt"] == 2
        assert message.headers["x-request-id"] == "req-42"
        assert message.delivery_mode == 2
        await message.ack()

    async def test_success_on_third_attempt_leaves_dlq_empty(self) -> None:
        payment_id = uuid4()
        self.outcomes = [WebhookDeliveryError(), WebhookDeliveryError(), None]

        await self._publish(payment_id)
        await wait_for(lambda: len(self.calls) == 3)
        await asyncio.sleep(0.5)

        assert await self.dead_letters.count() == 0
        assert len(self.calls) == 3

    async def test_invalid_message_goes_to_dlq_without_processing(self) -> None:
        await self.broker.publish(
            {"payment_id": "not-a-uuid"},
            exchange=payments_exchange,
            routing_key=payments_new_queue.routing_key,
        )

        await self._wait_dlq(1)

        assert self.calls == []

    async def test_requeue_returns_dead_letters_with_fresh_attempts(self) -> None:
        payment_id = uuid4()
        self.outcomes = [WebhookDeliveryError()] * 3
        await self._publish(payment_id)
        await self._wait_dlq(1)

        moved = await self.dead_letters.requeue()
        await wait_for(lambda: len(self.calls) == 4)
        await asyncio.sleep(0.5)

        assert moved == 1
        # Четвёртый вызов прошёл успешно: счётчик попыток сброшен, сообщение не вернулось в DLQ.
        assert await self.dead_letters.count() == 0
        assert len(self.calls) == 4

    async def test_requeue_respects_limit(self) -> None:
        for _ in range(3):
            await self.broker.publish({"broken": True}, exchange=payments_exchange, routing_key=config.RABBIT.QUEUE)
        await self._wait_dlq(3)

        moved = await self.dead_letters.requeue(limit=2)

        assert moved == 2
        # Невалидные сообщения после возврата снова отбраковываются, итог прежний: три в DLQ.
        await self._wait_dlq(3)

    async def test_unroutable_message_is_publish_error(self) -> None:
        with pytest.raises(EventPublishError):
            await self._publish(uuid4(), routing_key="no.such.queue")
