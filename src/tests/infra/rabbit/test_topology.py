from unittest.mock import AsyncMock

from faststream.rabbit import RabbitBroker
from pytest_mock import MockerFixture

from app.infra.rabbit.topology import (
    dead_letter_queue,
    declare_topology,
    payments_new_queue,
    retry_queues,
)


def test_main_queue_dead_letters_to_dlx() -> None:
    assert payments_new_queue.arguments == {"x-queue-type": "classic", "x-dead-letter-exchange": "payments.dlx"}
    assert dead_letter_queue.name == "payments.new.dlq"
    assert dead_letter_queue.routing_key == "payments.new"


def test_retry_queues_return_message_to_main_queue_after_ttl() -> None:
    assert [(queue.name, queue.arguments) for queue in retry_queues] == [
        (
            "payments.new.retry.1",
            {
                "x-queue-type": "classic",
                "x-message-ttl": 2000,
                "x-dead-letter-exchange": "payments",
                "x-dead-letter-routing-key": "payments.new",
            },
        ),
        (
            "payments.new.retry.2",
            {
                "x-queue-type": "classic",
                "x-message-ttl": 4000,
                "x-dead-letter-exchange": "payments",
                "x-dead-letter-routing-key": "payments.new",
            },
        ),
    ]


async def test_declare_topology_binds_every_queue(mocker: MockerFixture) -> None:
    broker: AsyncMock = mocker.AsyncMock(spec=RabbitBroker)

    await declare_topology(broker)

    declared = [call.args[0].name for call in broker.declare_queue.await_args_list]
    assert declared == ["payments.new", "payments.new.dlq", "payments.new.retry.1", "payments.new.retry.2"]
    bound = [call.kwargs["routing_key"] for call in broker.declare_queue.return_value.bind.await_args_list]
    assert bound == ["payments.new", "payments.new", "payments.new.retry.1", "payments.new.retry.2"]
