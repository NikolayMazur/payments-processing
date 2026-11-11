import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitBroker
from pytest_mock import MockerFixture

from app.app_layer.interfaces.publishers.events import EventPublishError
from app.config import RabbitSettings
from app.domain.outbox.entity import Outbox
from app.infra.rabbit.publisher import RabbitEventPublisher
from app.utils import utc_now


@pytest.fixture()
def record() -> Outbox:
    payment_id = str(uuid4())
    return Outbox(
        exchange="payments",
        routing_key="payments.new",
        event_key=payment_id,
        payload={"payment_id": payment_id},
        headers={"x-request-id": "req-42"},
        created_at=utc_now(),
    )


async def test_publish_persistent_message_with_outbox_id(record: Outbox, mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)

    await RabbitEventPublisher(broker=broker, settings=RabbitSettings()).publish(record)

    broker.publish.assert_awaited_once()
    call = broker.publish.await_args
    assert call.args == (record.payload,)
    assert call.kwargs["exchange"].name == "payments"
    assert call.kwargs["routing_key"] == "payments.new"
    assert call.kwargs["message_id"] == str(record.id)
    assert call.kwargs["headers"] == {"x-request-id": "req-42"}
    assert call.kwargs["persist"] is True
    assert call.kwargs["mandatory"] is True


async def test_ping_asks_broker_and_reports_answer(mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)

    assert await RabbitEventPublisher(broker=broker, settings=RabbitSettings()).ping() is True

    broker.declare_queue.return_value.declare.assert_awaited_once()


async def test_ping_is_false_when_broker_does_not_answer(mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)

    async def hang() -> None:
        await asyncio.sleep(60)

    # Соединение открыто, но брокер молчит: так выглядит зависший узел.
    broker.declare_queue.return_value.declare.side_effect = hang
    publisher = RabbitEventPublisher(broker=broker, settings=RabbitSettings(PING_TIMEOUT_SEC=0.05))

    assert await publisher.ping() is False


async def test_publish_to_silent_broker_fails_by_timeout(record: Outbox, mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)

    async def hang(*_: object, **__: object) -> None:
        await asyncio.sleep(60)

    broker.publish.side_effect = hang
    publisher = RabbitEventPublisher(broker=broker, settings=RabbitSettings(PUBLISH_TIMEOUT_SEC=0.05))

    with pytest.raises(EventPublishError):
        await asyncio.wait_for(publisher.publish(record), timeout=5)


async def test_broker_error_wrapped(record: Outbox, mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)
    broker.publish.side_effect = ConnectionError("broker is down")

    with pytest.raises(EventPublishError):
        await RabbitEventPublisher(broker=broker, settings=RabbitSettings()).publish(record)
