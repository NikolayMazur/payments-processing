import asyncio
from unittest.mock import AsyncMock

from pytest_mock import MockerFixture

from app.api.cli.outbox import send_outbox_until_stopped
from app.app_layer.interfaces.services.outbox.exceptions import OutboxPublishError
from app.app_layer.interfaces.services.outbox.services import AbstractSendOutboxService


async def test_relay_survives_errors_and_stops_on_event(mocker: MockerFixture) -> None:
    stop_event = asyncio.Event()
    service: AsyncMock = mocker.AsyncMock(spec=AbstractSendOutboxService)

    outcomes: list[int | Exception] = [3, OutboxPublishError(), ConnectionError(), 0]

    def process() -> int:
        outcome = outcomes.pop(0)
        if not outcomes:
            stop_event.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    service.process.side_effect = process

    await asyncio.wait_for(
        send_outbox_until_stopped(stop_event, service=service, poll_interval=0.001),
        timeout=5,
    )

    assert service.process.await_count == 4
