from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from faststream.rabbit import RabbitBroker
from pytest_mock import MockerFixture

from app.app_layer.interfaces.publishers.events import EventPublishError
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
        created_at=utc_now(),
    )


async def test_publish_persistent_message_with_outbox_id(record: Outbox, mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)

    await RabbitEventPublisher(broker=broker).publish(record)

    broker.publish.assert_awaited_once()
    call = broker.publish.await_args
    assert call.args == (record.payload,)
    assert call.kwargs["exchange"].name == "payments"
    assert call.kwargs["routing_key"] == "payments.new"
    assert call.kwargs["message_id"] == str(record.id)
    assert call.kwargs["persist"] is True
    assert call.kwargs["mandatory"] is True


async def test_broker_error_wrapped(record: Outbox, mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)
    broker.publish.side_effect = ConnectionError("broker is down")

    with pytest.raises(EventPublishError):
        await RabbitEventPublisher(broker=broker).publish(record)
