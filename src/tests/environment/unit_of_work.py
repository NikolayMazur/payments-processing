from sqlalchemy import func, select

from app.domain.outbox.entity import Outbox
from app.infra.repositories.sqla.models import OutboxOrm, PaymentOrm
from app.infra.unit_of_work.sqla import Uow


class TestUow(Uow):
    """UoW с выборками, которые нужны только тестам."""

    __test__ = False

    async def get_all_outbox(self) -> list[Outbox]:
        records = (await self._session.execute(select(OutboxOrm).order_by(OutboxOrm.created_at))).scalars().all()
        return [Outbox.model_validate(orm, from_attributes=True) for orm in records]

    async def count_payments(self) -> int:
        return (await self._session.execute(select(func.count()).select_from(PaymentOrm))).scalar_one()
