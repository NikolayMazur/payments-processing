from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.app_layer.interfaces.locks.payment import AbstractPaymentLock, PaymentLockedError

INT64_MASK = (1 << 63) - 1


class PgAdvisoryPaymentLock(AbstractPaymentLock):
    """Блокировка платежа на рекомендательных блокировках PostgreSQL.

    Блокировка привязана к соединению, а не к транзакции: её не нужно держать внутри долгой транзакции,
    и она снимается сама, если процесс умер и соединение закрылось.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def acquire(self, payment_id: UUID) -> AsyncIterator[None]:
        key = payment_id.int & INT64_MASK
        async with self._session_factory() as session:
            connection = await session.connection()
            is_acquired = (await connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})).scalar()
            if not is_acquired:
                raise PaymentLockedError
            try:
                yield
            finally:
                try:
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                except Exception:
                    # Снять блокировку не удалось: соединение нельзя возвращать в пул, иначе платёж
                    # останется занятым. Закрытое соединение блокировку отпустит.
                    await connection.invalidate()
                    raise
