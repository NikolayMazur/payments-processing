from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.infra.repositories.sqla.health import HealthRepository
from app.infra.repositories.sqla.outbox import OutboxRepository
from app.infra.repositories.sqla.payment import PaymentRepository


class Uow(AbcUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.payment = PaymentRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.health = HealthRepository(self._session)
        return await super().__aenter__()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def shutdown(self) -> None:
        await self._session.close()
