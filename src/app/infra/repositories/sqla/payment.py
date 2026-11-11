from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_layer.interfaces.repositories.payment import AbcPaymentRepository
from app.domain.payments.entity import Payment
from app.domain.payments.enums import PaymentStatusEnum
from app.infra.repositories.sqla.models import PaymentOrm


class PaymentRepository(AbcPaymentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_if_absent(self, entity: Payment) -> bool:
        stmt = (
            pg_insert(PaymentOrm)
            .values(
                id=entity.id,
                amount=entity.amount,
                currency=entity.currency,
                description=entity.description,
                metadata_=entity.metadata,
                status=entity.status,
                idempotency_key=entity.idempotency_key,
                request_hash=entity.request_hash,
                webhook_url=entity.webhook_url,
                failure_reason=entity.failure_reason,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
                processed_at=entity.processed_at,
            )
            .on_conflict_do_nothing(index_elements=[PaymentOrm.idempotency_key])
            .returning(PaymentOrm.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def get_by_id(self, payment_id: UUID) -> Payment | None:
        orm = (await self._session.execute(select(PaymentOrm).where(PaymentOrm.id == payment_id))).scalar_one_or_none()
        return self._map_model_to_entity(orm) if orm else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        stmt = select(PaymentOrm).where(PaymentOrm.idempotency_key == idempotency_key)
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._map_model_to_entity(orm) if orm else None

    async def set_result_if_pending(
        self,
        payment_id: UUID,
        status: PaymentStatusEnum,
        failure_reason: str | None,
        processed_at: datetime,
    ) -> Payment | None:
        stmt = (
            update(PaymentOrm)
            .where(PaymentOrm.id == payment_id, PaymentOrm.status == PaymentStatusEnum.PENDING)
            .values(status=status, failure_reason=failure_reason, processed_at=processed_at, updated_at=processed_at)
            .returning(PaymentOrm)
        )
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._map_model_to_entity(orm) if orm else None

    @staticmethod
    def _map_model_to_entity(orm: PaymentOrm) -> Payment:
        return Payment(
            id=orm.id,
            amount=orm.amount,
            currency=orm.currency,
            description=orm.description,
            metadata=orm.metadata_,
            status=orm.status,
            idempotency_key=orm.idempotency_key,
            request_hash=orm.request_hash,
            webhook_url=orm.webhook_url,
            failure_reason=orm.failure_reason,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
            processed_at=orm.processed_at,
        )
