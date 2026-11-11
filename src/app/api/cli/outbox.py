import asyncio
import math
import signal
import time
from logging import getLogger

from dependency_injector.wiring import Provide, inject

from app.app_layer import metrics
from app.app_layer.interfaces.publishers.dead_letters import AbstractDeadLetterQueue
from app.app_layer.interfaces.publishers.events import AbstractEventPublisher
from app.app_layer.interfaces.services.outbox.services import (
    AbstractCleanOutboxService,
    AbstractCollectQueueMetricsService,
    AbstractSendOutboxService,
)
from app.config import config
from app.containers import Container, shutdown_container
from app.infra.heartbeat import Heartbeat
from app.infra.metrics_server import start_metrics_server
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import declare_topology

logger = getLogger(__name__)


def get_error_delay(failures: int, poll_interval: float, max_delay: float) -> float:
    """Пауза после сбоя номер failures подряд: интервал опроса удваивается, пока не упрётся в потолок."""
    return float(min(poll_interval * 2**failures, max_delay))


async def _wait(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


@inject
async def send_outbox_until_stopped(
    stop_event: asyncio.Event,
    service: AbstractSendOutboxService = Provide[Container.send_outbox_service],
    publisher: AbstractEventPublisher = Provide[Container.event_publisher],
    heartbeat: Heartbeat = Provide[Container.heartbeat],
    poll_interval: float = config.OUTBOX.POLL_INTERVAL_SEC,
    max_error_delay: float = config.OUTBOX.MAX_ERROR_DELAY_SEC,
    heartbeat_interval: float = config.HEARTBEAT.INTERVAL_SEC,
) -> None:
    failures = 0
    last_ping_at = -math.inf
    while not stop_event.is_set():
        delay = poll_interval
        try:
            sent = await service.process()
        except Exception as exc:
            # Сбой БД или брокера не должен ронять процесс: записи остались PENDING и уйдут позже.
            # Пока сбой длится, паузу наращиваем, а трейсбек пишем только на первом, чтобы не засыпать лог.
            failures += 1
            metrics.outbox_publish_errors.inc()
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
            # Признак жизни обновляется после удачного круга и только при живом брокере. Без второй проверки
            # relay с пустым outbox выглядел бы здоровым, хотя отправить следующее событие ему некуда.
            # Брокер опрашивается не на каждом круге, а раз в интервал: это отдельный запрос по сети.
            if time.monotonic() - last_ping_at >= heartbeat_interval:
                last_ping_at = time.monotonic()
                if await publisher.ping():
                    heartbeat.beat()
            if failures:
                logger.info("Outbox relay recovered after %s failed iterations", failures)
                failures = 0
            if sent:
                logger.info("Outbox relay published %s records", sent)
                continue
        await _wait(stop_event, delay)


@inject
async def collect_metrics_until_stopped(
    stop_event: asyncio.Event,
    service: AbstractCollectQueueMetricsService = Provide[Container.collect_queue_metrics_service],
    interval: float = config.METRICS.COLLECT_INTERVAL_SEC,
) -> None:
    while not stop_event.is_set():
        try:
            await service.process()
        except Exception as exc:
            logger.warning("Could not collect queue metrics: %r", exc)
        await _wait(stop_event, interval)


async def run_outbox_relay(container: Container) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    start_metrics_server(config.METRICS)
    await broker.connect()
    await declare_topology(broker)
    logger.info("Outbox relay started")
    try:
        tasks = [send_outbox_until_stopped(stop_event)]
        if config.METRICS.ENABLED:
            tasks.append(collect_metrics_until_stopped(stop_event))
        await asyncio.gather(*tasks)
    finally:
        await broker.stop()
        await shutdown_container(container)
        logger.info("Outbox relay stopped")


@inject
async def run_clean_outbox(
    container: Container,
    retention_days: int | None,
    service: AbstractCleanOutboxService = Provide[Container.clean_outbox_service],
) -> int:
    try:
        return await service.process(retention_days=retention_days)
    finally:
        await shutdown_container(container)


@inject
async def run_requeue_dead_letters(
    container: Container,
    limit: int | None,
    dead_letter_queue: AbstractDeadLetterQueue = Provide[Container.dead_letter_queue],
) -> int:
    await broker.connect()
    await declare_topology(broker)
    try:
        return await dead_letter_queue.requeue(limit=limit)
    finally:
        await broker.stop()
        await shutdown_container(container)
