from datetime import datetime

from app.app_layer import metrics
from app.app_layer.interfaces.publishers.dead_letters import AbstractDeadLetterQueue
from app.app_layer.interfaces.services.outbox.services import AbstractCollectQueueMetricsService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.utils import utc_now


class CollectQueueMetricsService(AbstractCollectQueueMetricsService):
    """Обновляет метрики отставания: события в outbox, платежи без итога и сообщения в DLQ."""

    def __init__(self, uow: AbcUnitOfWork, dead_letter_queue: AbstractDeadLetterQueue) -> None:
        self._uow = uow
        self._dead_letter_queue = dead_letter_queue

    async def process(self) -> None:
        async with self._uow:
            outbox = await self._uow.outbox.get_pending_stats()
            payments = await self._uow.payment.get_pending_stats()
        metrics.outbox_pending.set(outbox.count)
        metrics.outbox_oldest_pending_age.set(self._age(outbox.oldest_created_at))
        metrics.payments_pending.set(payments.count)
        metrics.payments_oldest_pending_age.set(self._age(payments.oldest_created_at))
        metrics.dead_letter_queue_messages.set(await self._dead_letter_queue.count())

    @staticmethod
    def _age(moment: datetime | None) -> float:
        return max((utc_now() - moment).total_seconds(), 0.0) if moment else 0.0
