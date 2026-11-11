from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from app.domain.outbox.entity import Outbox


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
