from dependency_injector.wiring import Provide, inject

from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.services import AbstractProcessPaymentService
from app.containers import Container
from app.domain.payments.events import PaymentCreatedEvent


@inject
async def process_payment_created_event(
    data: PaymentCreatedEvent,
    service: AbstractProcessPaymentService = Provide[Container.process_payment_service],
) -> None:
    await service.process(ProcessPaymentInputDTO(payment_id=data.payment_id))
