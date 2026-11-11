from types import TracebackType
from typing import Self

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.app_layer.interfaces.unit_of_work.exceptions import DatabaseUnavailableError
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.infra.repositories.sqla.health import HealthRepository
from app.infra.repositories.sqla.outbox import OutboxRepository
from app.infra.repositories.sqla.payment import PaymentRepository


def is_connection_error(exc: BaseException) -> bool:
    """Отличает потерю связи с БД от ошибки в запросе или данных."""
    if isinstance(exc, DBAPIError):
        return exc.connection_invalidated or isinstance(exc, OperationalError | InterfaceError)
    return isinstance(exc, OSError | TimeoutError)


class Uow(AbcUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.payment = PaymentRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.health = HealthRepository(self._session)
        return await super().__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Сбой соединения наружу уходит одной ошибкой прикладного слоя, чтобы вызывающий код
        # не зависел от драйвера и мог ответить «повторите позже».
        try:
            await super().__aexit__(exc_type, exc_val, exc_tb)
        except Exception as exc:
            if is_connection_error(exc):
                raise DatabaseUnavailableError from exc
            raise
        if exc_val is not None and is_connection_error(exc_val):
            raise DatabaseUnavailableError from exc_val

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def shutdown(self) -> None:
        await self._session.close()
