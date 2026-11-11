import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from app.api.cli.outbox import collect_metrics_until_stopped, get_error_delay, send_outbox_until_stopped
from app.app_layer.interfaces.publishers.events import AbstractEventPublisher
from app.app_layer.interfaces.services.outbox.exceptions import OutboxPublishError
from app.app_layer.interfaces.services.outbox.services import (
    AbstractCollectQueueMetricsService,
    AbstractSendOutboxService,
)
from app.infra.heartbeat import Heartbeat


def build_service(mocker: MockerFixture, stop_event: asyncio.Event, outcomes: list[int | Exception]) -> AsyncMock:
    service: AsyncMock = mocker.AsyncMock(spec=AbstractSendOutboxService)

    def process() -> int:
        outcome = outcomes.pop(0)
        if not outcomes:
            stop_event.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    service.process.side_effect = process
    return service


@pytest.mark.parametrize(
    ("failures", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (6, 30.0), (50, 30.0)],
)
def test_error_delay_doubles_up_to_limit(failures: int, expected: float) -> None:
    assert get_error_delay(failures, poll_interval=0.5, max_delay=30) == expected


async def test_relay_survives_errors_and_stops_on_event(mocker: MockerFixture) -> None:
    heartbeat: MagicMock = mocker.MagicMock(spec=Heartbeat)
    publisher: AsyncMock = mocker.AsyncMock(spec=AbstractEventPublisher)
    publisher.ping.return_value = True
    stop_event = asyncio.Event()
    service = build_service(mocker, stop_event, [3, OutboxPublishError(), ConnectionError(), 0])

    await asyncio.wait_for(
        send_outbox_until_stopped(
            stop_event,
            service=service,
            publisher=publisher,
            heartbeat=heartbeat,
            poll_interval=0.001,
            max_error_delay=0.002,
        ),
        timeout=5,
    )

    assert service.process.await_count == 4
    # Признак жизни обновляется только после удачных кругов: их два из четырёх.
    assert heartbeat.beat.call_count == 2


async def test_relay_logs_traceback_only_for_first_error_in_a_row(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    heartbeat: MagicMock = mocker.MagicMock(spec=Heartbeat)
    publisher: AsyncMock = mocker.AsyncMock(spec=AbstractEventPublisher)
    publisher.ping.return_value = True
    stop_event = asyncio.Event()
    errors: list[int | Exception] = [ConnectionError("broker is down") for _ in range(4)]
    service = build_service(mocker, stop_event, [*errors, 0])

    with caplog.at_level(logging.INFO, logger="app.api.cli.outbox"):
        await asyncio.wait_for(
            send_outbox_until_stopped(
                stop_event,
                service=service,
                publisher=publisher,
                heartbeat=heartbeat,
                poll_interval=0.001,
                max_error_delay=0.002,
            ),
            timeout=5,
        )

    with_traceback = [record for record in caplog.records if record.exc_info]
    assert len(with_traceback) == 1
    assert [record.levelname for record in caplog.records] == ["ERROR", "WARNING", "WARNING", "WARNING", "INFO"]
    assert "recovered after 4 failed iterations" in caplog.records[-1].getMessage()


async def test_metrics_collector_survives_errors(mocker: MockerFixture) -> None:
    stop_event = asyncio.Event()
    service: AsyncMock = mocker.AsyncMock(spec=AbstractCollectQueueMetricsService)
    outcomes: list[Exception | None] = [ConnectionError(), None]

    def process() -> None:
        outcome = outcomes.pop(0)
        if not outcomes:
            stop_event.set()
        if outcome is not None:
            raise outcome

    service.process.side_effect = process

    await asyncio.wait_for(collect_metrics_until_stopped(stop_event, service=service, interval=0.001), timeout=5)

    assert service.process.await_count == 2


async def test_relay_does_not_beat_without_broker(mocker: MockerFixture) -> None:
    heartbeat: MagicMock = mocker.MagicMock(spec=Heartbeat)
    publisher: AsyncMock = mocker.AsyncMock(spec=AbstractEventPublisher)
    publisher.ping.return_value = False
    stop_event = asyncio.Event()
    # Outbox пуст, круги проходят без ошибок, но брокера нет.
    service = build_service(mocker, stop_event, [0, 0, 0])

    await asyncio.wait_for(
        send_outbox_until_stopped(
            stop_event,
            service=service,
            publisher=publisher,
            heartbeat=heartbeat,
            poll_interval=0.001,
            max_error_delay=0.002,
        ),
        timeout=5,
    )

    heartbeat.beat.assert_not_called()
