from datetime import timedelta
from logging import getLogger

from app.app_layer.interfaces.services.outbox.services import AbstractCleanOutboxService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.config import OutboxSettings
from app.utils import utc_now

logger = getLogger(__name__)


class CleanOutboxService(AbstractCleanOutboxService):
    """Удаляет опубликованные записи outbox, которые старше срока хранения."""

    def __init__(self, uow: AbcUnitOfWork, settings: OutboxSettings) -> None:
        self._uow = uow
        self._settings = settings

    async def process(self, retention_days: int | None = None) -> int:
        days = self._settings.RETENTION_DAYS if retention_days is None else retention_days
        threshold = utc_now() - timedelta(days=days)
        total = 0
        # Удаляем пачками в отдельных транзакциях, чтобы не держать длинную блокировку и не раздувать WAL.
        while True:
            async with self._uow:
                deleted = await self._uow.outbox.delete_processed_before(
                    threshold=threshold,
                    limit=self._settings.CLEAN_BATCH_SIZE,
                )
            total += deleted
            if deleted < self._settings.CLEAN_BATCH_SIZE:
                break
        logger.info("Deleted %s outbox records older than %s days", total, days)
        return total
