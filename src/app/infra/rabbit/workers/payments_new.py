"""Консьюмер очереди payments.new.

Запуск: faststream run app.infra.rabbit.workers.payments_new:app
"""

import asyncio
from logging import getLogger
from typing import Any

from faststream import AckPolicy, FastStream
from faststream.rabbit import RabbitMessage
from prometheus_client import start_http_server
from pydantic import ValidationError

from app.api.events.rabbit.consumers import payment_created
from app.api.events.rabbit.consumers.payment_created import process_payment_created_event
from app.app_layer import metrics
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.config import config
from app.containers import Container, shutdown_container
from app.context import REQUEST_ID_MESSAGE_HEADER, request_id_var
from app.domain.payments.events import PaymentCreatedEvent
from app.infra.heartbeat import Heartbeat
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import (
    declare_topology,
    payments_exchange,
    payments_new_queue,
    retry_exchange,
    retry_service,
)
from app.logs import init_logging

init_logging(config)
logger = getLogger(__name__)

PING_TIMEOUT_SEC = 3

app = FastStream(broker, logger=logger)


async def beat_while_broker_is_reachable(heartbeat: Heartbeat) -> None:
    """Обновляет признак жизни, пока есть связь с брокером. По нему работает healthcheck контейнера."""
    while True:
        if await broker.ping(timeout=PING_TIMEOUT_SEC):
            heartbeat.beat()
        await asyncio.sleep(config.HEARTBEAT.INTERVAL_SEC)


@app.on_startup
async def startup() -> None:
    container = Container()
    container.wire(modules=[payment_created])
    app.context.set_global("container", container)
    if config.METRICS.ENABLED:
        start_http_server(config.METRICS.PORT)
    await broker.connect()
    await declare_topology(broker)


@app.after_startup
async def start_heartbeat() -> None:
    container: Container = app.context.get("container")
    app.context.set_global("heartbeat_task", asyncio.create_task(beat_while_broker_is_reachable(container.heartbeat())))


@app.after_shutdown
async def shutdown() -> None:
    app.context.get("heartbeat_task").cancel()
    await shutdown_container(app.context.get("container"))


@broker.subscriber(payments_new_queue, payments_exchange, ack_policy=AckPolicy.MANUAL)
async def payments_new_consumer(body: Any, message: RabbitMessage) -> None:
    # Идентификатор запроса клиента едет в заголовке сообщения, с ним логи API и консьюмера собираются в одну цепочку.
    token = request_id_var.set(message.headers.get(REQUEST_ID_MESSAGE_HEADER))
    try:
        await _handle(body=body, message=message)
    finally:
        request_id_var.reset(token)


async def _handle(body: Any, message: RabbitMessage) -> None:
    try:
        event = PaymentCreatedEvent.model_validate(body)
    except ValidationError:
        # Повтор не поможет: сообщение с таким телом не разберётся никогда.
        logger.exception("Invalid message %s in %s, sent to DLQ", message.message_id, config.RABBIT.QUEUE)
        metrics.messages_dead_lettered.labels(reason="invalid").inc()
        await message.reject(requeue=False)
        return

    try:
        await process_payment_created_event(event)
    except PaymentNotFoundError:
        logger.exception("Payment %s not found, message sent to DLQ", event.payment_id)
        metrics.messages_dead_lettered.labels(reason="not_found").inc()
        await message.reject(requeue=False)
        return
    except Exception as exc:
        await _retry_or_reject(body=body, message=message, event=event, exc=exc)
        return

    await message.ack()


async def _retry_or_reject(body: Any, message: RabbitMessage, event: PaymentCreatedEvent, exc: Exception) -> None:
    retry_attempt = retry_service.extract_retry_attempt(message.headers)
    if retry_service.has_exhausted_attempts(retry_attempt):
        logger.error(
            "Payment %s is not processed after %s attempts, message sent to DLQ",
            event.payment_id,
            config.RABBIT.MAX_ATTEMPTS,
            exc_info=exc,
        )
        metrics.messages_dead_lettered.labels(reason="attempts_exhausted").inc()
        # Очередь объявлена с x-dead-letter-exchange, поэтому reject без requeue уводит сообщение в DLQ.
        await message.reject(requeue=False)
        return

    delay = retry_service.get_delay_sec(retry_attempt)
    logger.warning(
        "Payment %s processing failed on attempt %s, retry in %s sec: %r",
        event.payment_id,
        retry_attempt + 1,
        delay,
        exc,
    )
    # Сначала публикуем копию в очередь задержки и только потом подтверждаем исходное сообщение.
    # Если публикация упадёт, сообщение останется неподтверждённым и брокер доставит его заново.
    await broker.publish(
        body,
        exchange=retry_exchange,
        routing_key=retry_service.get_retry_queue_name(retry_attempt),
        headers=retry_service.build_retry_headers(message.headers, retry_attempt),
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        persist=True,
        mandatory=True,
    )
    metrics.messages_retried.inc()
    await message.ack()
