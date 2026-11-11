from abc import ABC, abstractmethod
from uuid import UUID

from app.app_layer.interfaces.services.payments.dto import (
    CreatePaymentInputDTO,
    CreatePaymentOutputDTO,
    PaymentOutputDTO,
    ProcessPaymentInputDTO,
)


class AbstractCreatePaymentService(ABC):
    @abstractmethod
    async def process(self, data: CreatePaymentInputDTO) -> CreatePaymentOutputDTO:
        raise NotImplementedError


class AbstractGetPaymentService(ABC):
    @abstractmethod
    async def process(self, payment_id: UUID) -> PaymentOutputDTO:
        raise NotImplementedError


class AbstractProcessPaymentService(ABC):
    @abstractmethod
    async def process(self, data: ProcessPaymentInputDTO) -> None:
        raise NotImplementedError
