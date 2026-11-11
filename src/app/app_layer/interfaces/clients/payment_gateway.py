from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.domain.payments.entity import Payment


class GatewayChargeResultDTO(BaseModel):
    is_success: bool
    failure_reason: str | None = None


class AbstractPaymentGateway(ABC):
    @abstractmethod
    async def charge(self, payment: Payment) -> GatewayChargeResultDTO:
        raise NotImplementedError
