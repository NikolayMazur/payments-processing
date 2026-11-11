from app.app_layer import metrics
from app.app_layer.interfaces.publishers.dead_letters import AbstractDeadLetterQueue
from app.app_layer.interfaces.services.outbox.services import AbstractCollectQueueMetricsService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.utils import utc_now


class CollectQueueMetricsService(AbstractCollectQueueMetricsService):
    """Обновляет метрики отставания: сколько событий ждёт в outbox и сколько сообщений лежит в DLQ."""

    def __init__(self, uow: AbcUnitOfWork, dead_letter_queue: AbstractDeadLetterQueue) -> None:
        self._uow = uow
        self._dead_letter_queue = dead_letter_queue

    async def process(self) -> None:
        async with self._uow:
            stats = await self._uow.outbox.get_pending_stats()
        age = (utc_now() - stats.oldest_created_at).total_seconds() if stats.oldest_created_at else 0.0
        metrics.outbox_pending.set(stats.count)
        metrics.outbox_oldest_pending_age.set(max(age, 0.0))
        metrics.dead_letter_queue_messages.set(await self._dead_letter_queue.count())
