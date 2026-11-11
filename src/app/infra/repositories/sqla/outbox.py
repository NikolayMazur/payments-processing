from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_layer.interfaces.repositories.outbox import AbcOutboxRepository
from app.domain.outbox.entity import Outbox
from app.domain.outbox.enums import OutboxStatusEnum
from app.infra.repositories.sqla.models import OutboxOrm


class OutboxRepository(AbcOutboxRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_from_entity(self, entity: Outbox) -> Outbox:
        await self._session.execute(insert(OutboxOrm).values(**entity.model_dump()))
        return entity

    async def get_pending_batch_for_update(self, limit: int) -> list[Outbox]:
        stmt = (
            select(OutboxOrm)
            .where(OutboxOrm.status == OutboxStatusEnum.PENDING)
            .order_by(OutboxOrm.created_at)
            .limit(limit)
            # SKIP LOCKED даёт запускать несколько relay-воркеров: каждый берёт свои строки и не ждёт соседа.
            .with_for_update(skip_locked=True)
        )
        records = (await self._session.execute(stmt)).scalars().all()
        return [Outbox.model_validate(orm, from_attributes=True) for orm in records]

    async def mark_batch_success(self, ids: Sequence[UUID], processed_at: datetime) -> None:
        await self._session.execute(
            update(OutboxOrm)
            .where(OutboxOrm.id.in_(ids))
            .values(status=OutboxStatusEnum.SUCCESS, processed_at=processed_at),
        )
