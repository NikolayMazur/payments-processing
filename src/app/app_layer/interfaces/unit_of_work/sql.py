from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from app.app_layer.interfaces.repositories.health import AbcHealthRepository
from app.app_layer.interfaces.repositories.outbox import AbcOutboxRepository
from app.app_layer.interfaces.repositories.payment import AbcPaymentRepository


class AbcUnitOfWork(ABC):
    payment: AbcPaymentRepository
    outbox: AbcOutboxRepository
    health: AbcHealthRepository

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
            else:
                await self.commit()
        finally:
            await self.shutdown()

    @abstractmethod
    async def commit(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def rollback(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def shutdown(self) -> None:
        raise NotImplementedError
