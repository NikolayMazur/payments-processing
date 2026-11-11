from logging import getLogger

from faststream.rabbit import RabbitBroker

from app.app_layer.interfaces.publishers.dead_letters import AbstractDeadLetterQueue
from app.config import RabbitSettings
from app.infra.rabbit.topology import dead_letter_queue, payments_exchange, payments_new_queue

logger = getLogger(__name__)

# Заголовки, которыми брокер помечает путь сообщения до DLQ. При возврате в работу они не нужны.
DEATH_HEADER_PREFIXES = ("x-death", "x-first-death", "x-last-death")
GET_TIMEOUT_SEC = 5


class RabbitDeadLetterQueue(AbstractDeadLetterQueue):
    def __init__(self, broker: RabbitBroker, settings: RabbitSettings) -> None:
        self._broker = broker
        self._settings = settings

    async def count(self) -> int:
        queue = await self._broker.declare_queue(dead_letter_queue)
        # Брокер FastStream кеширует объявленные очереди, поэтому за свежим счётчиком объявляем заново.
        result = await queue.declare()
        return int(result.message_count or 0)

    async def requeue(self, limit: int | None = None) -> int:
        available = await self.count()
        # Берём не больше, чем лежало на старте: если сообщения снова упадут в DLQ, по кругу их не гоняем.
        to_move = available if limit is None else min(limit, available)
        queue = await self._broker.declare_queue(dead_letter_queue)
        moved = 0
        for _ in range(to_move):
            message = await queue.get(fail=False, timeout=GET_TIMEOUT_SEC)
            if message is None:
                break
            headers = {
                key: value
                for key, value in message.headers.items()
                if key != self._settings.RETRY_HEADER and not key.startswith(DEATH_HEADER_PREFIXES)
            }
            # Сначала публикуем, потом подтверждаем: при сбое сообщение останется в DLQ.
            await self._broker.publish(
                message.body,
                exchange=payments_exchange,
                routing_key=payments_new_queue.routing_key,
                headers=headers,
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                content_type=message.content_type,
                persist=True,
                mandatory=True,
            )
            await message.ack()
            moved += 1
        logger.info("Requeued %s of %s dead letters", moved, available)
        return moved
