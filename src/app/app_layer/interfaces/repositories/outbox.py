from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.outbox.entity import Outbox


class OutboxPendingStatsDTO(BaseModel):
    count: int
    oldest_created_at: datetime | None


class AbcOutboxRepository(ABC):
    @abstractmethod
    async def create_from_entity(self, entity: Outbox) -> Outbox:
        raise NotImplementedError

    @abstractmethod
    async def get_pending_batch_for_update(self, limit: int) -> list[Outbox]:
        """Берёт пачку неотправленных записей под блокировку, пропуская занятые другими воркерами."""
        raise NotImplementedError

    @abstractmethod
    async def mark_batch_success(self, ids: Sequence[UUID], processed_at: datetime) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_pending_stats(self) -> OutboxPendingStatsDTO:
        raise NotImplementedError

    @abstractmethod
    async def delete_processed_before(self, threshold: datetime, limit: int) -> int:
        """Удаляет пачку опубликованных записей старше threshold. Возвращает число удалённых."""
        raise NotImplementedError
