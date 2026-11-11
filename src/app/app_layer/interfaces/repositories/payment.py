from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.domain.payments.entity import Payment
from app.domain.payments.enums import PaymentStatusEnum


class AbcPaymentRepository(ABC):
    @abstractmethod
    async def create_if_absent(self, entity: Payment) -> bool:
        """Создаёт платёж, если ключ идемпотентности ещё не занят. Возвращает признак создания."""
        raise NotImplementedError

    @abstractmethod
    async def get_by_id(self, payment_id: UUID) -> Payment | None:
        raise NotImplementedError

    @abstractmethod
    async def get_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        raise NotImplementedError

    @abstractmethod
    async def set_result_if_pending(
        self,
        payment_id: UUID,
        status: PaymentStatusEnum,
        failure_reason: str | None,
        processed_at: datetime,
    ) -> Payment | None:
        """Проставляет итог обработки платежу в статусе pending. Если платёж уже обработан, возвращает None."""
        raise NotImplementedError

    @abstractmethod
    async def register_webhook_attempt(self, payment_id: UUID, delivered_at: datetime | None) -> None:
        """Учитывает попытку доставки вебхука. delivered_at передаётся, если получатель его принял."""
        raise NotImplementedError
