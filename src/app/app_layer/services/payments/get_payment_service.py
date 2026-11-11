from uuid import UUID

from app.app_layer.interfaces.services.payments.dto import PaymentOutputDTO
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.app_layer.interfaces.services.payments.services import AbstractGetPaymentService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork


class GetPaymentService(AbstractGetPaymentService):
    def __init__(self, uow: AbcUnitOfWork) -> None:
        self._uow = uow

    async def process(self, payment_id: UUID) -> PaymentOutputDTO:
        async with self._uow:
            payment = await self._uow.payment.get_by_id(payment_id)
        if payment is None:
            raise PaymentNotFoundError
        return PaymentOutputDTO(payment_id=payment.id, **payment.model_dump(exclude={"id"}))
