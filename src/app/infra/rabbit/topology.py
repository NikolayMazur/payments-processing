"""Обменники и очереди сервиса.

payments (direct) ── payments.new ──▶ payments.new (очередь, DLX = payments.dlx)
payments.retry (direct) ── payments.new.retry.N ──▶ очередь с TTL, по истечении сообщение
                                                     возвращается в payments с ключом payments.new
payments.dlx (direct) ── payments.new ──▶ payments.new.dlq
"""

import asyncio

from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from app.config import config
from app.infra.rabbit.retry import RetryService

MS_IN_SEC = 1000

retry_service = RetryService(config.RABBIT)

payments_exchange = RabbitExchange(config.RABBIT.EXCHANGE, durable=True)
retry_exchange = RabbitExchange(config.RABBIT.RETRY_EXCHANGE, durable=True)
dead_letter_exchange = RabbitExchange(config.RABBIT.DLX, durable=True)

payments_new_queue = RabbitQueue(
    config.RABBIT.QUEUE,
    durable=True,
    routing_key=config.RABBIT.QUEUE,
    arguments={"x-dead-letter-exchange": config.RABBIT.DLX},
)
dead_letter_queue = RabbitQueue(config.RABBIT.DLQ, durable=True, routing_key=config.RABBIT.QUEUE)

# На каждую задержку своя очередь: в общей очереди сообщение с длинным TTL задерживало бы короткие за собой.
retry_queues = [
    RabbitQueue(
        retry_service.get_retry_queue_name(retry_attempt),
        durable=True,
        routing_key=retry_service.get_retry_queue_name(retry_attempt),
        arguments={
            "x-message-ttl": retry_service.get_delay_sec(retry_attempt) * MS_IN_SEC,
            "x-dead-letter-exchange": config.RABBIT.EXCHANGE,
            "x-dead-letter-routing-key": config.RABBIT.QUEUE,
        },
    )
    for retry_attempt in range(retry_service.retries_qty)
]


async def is_broker_responsive(broker: RabbitBroker, timeout_sec: float) -> bool:
    """Проверяет брокер запросом с ответом, а не состоянием соединения.

    Соединение с зависшим брокером выглядит открытым, пока не сработает heartbeat протокола,
    поэтому признак «подключён» здесь не годится.
    """
    try:
        async with asyncio.timeout(timeout_sec):
            queue = await broker.declare_queue(payments_new_queue)
            await queue.declare()
    except Exception:
        return False
    return True


async def declare_topology(broker: RabbitBroker) -> None:
    """Объявляет всю топологию. Вызывается и в relay, и в консьюмере, чтобы порядок их запуска был не важен."""
    bindings = [
        (payments_exchange, payments_new_queue),
        (dead_letter_exchange, dead_letter_queue),
        *((retry_exchange, queue) for queue in retry_queues),
    ]
    for exchange, queue in bindings:
        declared_exchange = await broker.declare_exchange(exchange)
        declared_queue = await broker.declare_queue(queue)
        await declared_queue.bind(declared_exchange, routing_key=queue.routing_key)
