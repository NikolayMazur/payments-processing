import asyncio
import signal
from logging import getLogger

from dependency_injector.wiring import Provide, inject

from app.app_layer.interfaces.services.outbox.services import AbstractSendOutboxService
from app.config import config
from app.containers import Container, shutdown_container
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import declare_topology

logger = getLogger(__name__)


def get_error_delay(failures: int, poll_interval: float, max_delay: float) -> float:
    """Пауза после сбоя номер failures подряд: интервал опроса удваивается, пока не упрётся в потолок."""
    return float(min(poll_interval * 2**failures, max_delay))


@inject
async def send_outbox_until_stopped(
    stop_event: asyncio.Event,
    service: AbstractSendOutboxService = Provide[Container.send_outbox_service],
    poll_interval: float = config.OUTBOX.POLL_INTERVAL_SEC,
    max_error_delay: float = config.OUTBOX.MAX_ERROR_DELAY_SEC,
) -> None:
    failures = 0
    while not stop_event.is_set():
        delay = poll_interval
        try:
            sent = await service.process()
        except Exception as exc:
            # Сбой БД или брокера не должен ронять процесс: записи остались PENDING и уйдут позже.
            # Пока сбой длится, паузу наращиваем, а трейсбек пишем только на первом, чтобы не засыпать лог.
            failures += 1
            delay = get_error_delay(failures, poll_interval, max_error_delay)
            if failures == 1:
                logger.exception("Outbox relay iteration failed, next try in %.1f sec", delay)
            else:
                logger.warning(
                    "Outbox relay is still failing, %s errors in a row, next try in %.1f sec: %r",
                    failures,
                    delay,
                    exc.__cause__ or exc,
                )
        else:
            if failures:
                logger.info("Outbox relay recovered after %s failed iterations", failures)
                failures = 0
            if sent:
                logger.info("Outbox relay published %s records", sent)
                continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass


async def run_outbox_relay(container: Container) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await broker.connect()
    await declare_topology(broker)
    logger.info("Outbox relay started")
    try:
        await send_outbox_until_stopped(stop_event)
    finally:
        await broker.stop()
        await shutdown_container(container)
        logger.info("Outbox relay stopped")
