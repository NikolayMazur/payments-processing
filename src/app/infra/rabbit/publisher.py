from faststream.rabbit import RabbitBroker, RabbitExchange

from app.app_layer.interfaces.publishers.events import AbstractEventPublisher, EventPublishError
from app.domain.outbox.entity import Outbox


class RabbitEventPublisher(AbstractEventPublisher):
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish(self, record: Outbox) -> None:
        try:
            # Канал работает с publisher confirms, publish возвращается только после подтверждения брокера.
            # mandatory не даёт молча потерять сообщение, если очередь не привязана.
            await self._broker.publish(
                record.payload,
                exchange=RabbitExchange(record.exchange, durable=True, declare=False),
                routing_key=record.routing_key,
                message_id=str(record.id),
                correlation_id=record.event_key,
                persist=True,
                mandatory=True,
            )
        except Exception as exc:
            raise EventPublishError(f"Could not publish outbox record {record.id}") from exc
