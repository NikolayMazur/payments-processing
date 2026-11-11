from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from uuid import UUID


class PaymentLockedError(Exception):
    """Платёж прямо сейчас обрабатывает другой обработчик"""


class AbstractPaymentLock(ABC):
    @abstractmethod
    def acquire(self, payment_id: UUID) -> AbstractAsyncContextManager[None]:
        """Захватывает платёж на время обработки. Если он уже занят, бросает PaymentLockedError и не ждёт."""
        raise NotImplementedError
