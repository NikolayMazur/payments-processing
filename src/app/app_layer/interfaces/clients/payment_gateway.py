from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.domain.payments.entity import Payment


class GatewayChargeResultDTO(BaseModel):
    is_success: bool
    failure_reason: str | None = None


class AbstractPaymentGateway(ABC):
    @abstractmethod
    async def charge(self, payment: Payment) -> GatewayChargeResultDTO:
        """Проводит платёж через шлюз.

        Реализация обязана быть идемпотентной по payment.id: передавать его шлюзу как ключ идемпотентности.
        Сервис может вызвать метод для одного платежа повторно, если после ответа шлюза не удалось
        сохранить итог (сбой БД, падение процесса) или одно событие получили два консьюмера.
        """
        raise NotImplementedError
