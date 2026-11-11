from typing import Any

from faststream.rabbit import RabbitBroker, RabbitExchange

from app.app_layer.interfaces.publishers.events import AbstractEventPublisher, EventPublishError
from app.domain.outbox.entity import Outbox

PING_TIMEOUT_SEC = 3


class RabbitEventPublisher(AbstractEventPublisher):
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish(self, record: Outbox) -> None:
        headers: dict[str, Any] = dict(record.headers)
        try:
            # Канал работает с publisher confirms, publish возвращается только после подтверждения брокера.
            # mandatory не даёт молча потерять сообщение, если очередь не привязана.
            await self._broker.publish(
                record.payload,
                exchange=RabbitExchange(record.exchange, durable=True, declare=False),
                routing_key=record.routing_key,
                headers=headers or None,
                message_id=str(record.id),
                correlation_id=record.event_key,
                persist=True,
                mandatory=True,
            )
        except Exception as exc:
            raise EventPublishError(f"Could not publish outbox record {record.id}") from exc

    async def ping(self) -> bool:
        return await self._broker.ping(timeout=PING_TIMEOUT_SEC)
