import asyncio
import signal
from logging import getLogger

from dependency_injector.wiring import Provide, inject

from app.app_layer.interfaces.services.outbox.exceptions import OutboxPublishError
from app.app_layer.interfaces.services.outbox.services import AbstractSendOutboxService
from app.config import config
from app.containers import Container, shutdown_container
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import declare_topology

logger = getLogger(__name__)


@inject
async def send_outbox_until_stopped(
    stop_event: asyncio.Event,
    service: AbstractSendOutboxService = Provide[Container.send_outbox_service],
    poll_interval: float = config.OUTBOX.POLL_INTERVAL_SEC,
) -> None:
    while not stop_event.is_set():
        try:
            sent = await service.process()
        except OutboxPublishError:
            sent = 0
        except Exception:
            # Сбой БД или брокера не должен ронять процесс: записи остались PENDING и уйдут на следующем круге.
            logger.exception("Outbox relay iteration failed")
            sent = 0
        if sent:
            logger.info("Outbox relay published %s records", sent)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
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
