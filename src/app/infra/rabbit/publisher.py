import asyncio
from typing import Any

from faststream.rabbit import RabbitBroker, RabbitExchange

from app.app_layer.interfaces.publishers.events import AbstractEventPublisher, EventPublishError
from app.config import RabbitSettings
from app.domain.outbox.entity import Outbox
from app.infra.rabbit.topology import is_broker_responsive


class RabbitEventPublisher(AbstractEventPublisher):
    def __init__(self, broker: RabbitBroker, settings: RabbitSettings) -> None:
        self._broker = broker
        self._settings = settings

    async def publish(self, record: Outbox) -> None:
        headers: dict[str, Any] = dict(record.headers)
        try:
            # Канал работает с publisher confirms, publish возвращается только после подтверждения брокера.
            # mandatory не даёт молча потерять сообщение, если очередь не привязана.
            # Предел по времени нужен на случай брокера, который принял соединение и перестал отвечать:
            # без него relay завис бы вместе с открытой транзакцией. Если подтверждение всё же придёт позже,
            # запись уйдёт второй раз, дубль погасит потребитель.
            async with asyncio.timeout(self._settings.PUBLISH_TIMEOUT_SEC):
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
        return await is_broker_responsive(self._broker, timeout_sec=self._settings.PING_TIMEOUT_SEC)
