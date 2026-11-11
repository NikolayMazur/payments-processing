from typing import Annotated
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.rest.schemas import DetailErrorSchema
from app.api.rest.v1.schemas import CreatePaymentRequestSchema
from app.app_layer.interfaces.services.payments.dto import (
    CreatePaymentInputDTO,
    CreatePaymentOutputDTO,
    PaymentOutputDTO,
)
from app.app_layer.interfaces.services.payments.exceptions import IdempotencyKeyConflictError, PaymentNotFoundError
from app.app_layer.interfaces.services.payments.services import AbstractCreatePaymentService, AbstractGetPaymentService
from app.containers import Container
from app.errors import DUPLICATE_IDEMPOTENCY_KEY_ERROR, IDEMPOTENCY_KEY_CONFLICT_ERROR, PAYMENT_NOT_FOUND_ERROR

router = APIRouter(prefix="/payments", tags=["Payments"])

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


async def get_idempotency_key(
    request: Request,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER, min_length=1, max_length=255)],
) -> str:
    # Два разных ключа в одном запросе: непонятно, по какому из них защищать от дубля.
    if len(request.headers.getlist(IDEMPOTENCY_KEY_HEADER)) > 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=DUPLICATE_IDEMPOTENCY_KEY_ERROR)
    return idempotency_key


@router.post(
    "",
    response_model=CreatePaymentOutputDTO,
    name="create_payment",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Тело запроса не разбирается как JSON или Idempotency-Key передан несколько раз",
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": DetailErrorSchema},
        status.HTTP_409_CONFLICT: {"model": DetailErrorSchema},
    },
)
@inject
async def create_payment(
    data: CreatePaymentRequestSchema,
    idempotency_key: Annotated[str, Depends(get_idempotency_key)],
    service: Annotated[AbstractCreatePaymentService, Depends(Provide[Container.create_payment_service])],
) -> CreatePaymentOutputDTO:
    try:
        return await service.process(
            CreatePaymentInputDTO(
                idempotency_key=idempotency_key,
                amount=data.amount,
                currency=data.currency,
                description=data.description,
                metadata=data.metadata,
                webhook_url=str(data.webhook_url),
            ),
        )
    except IdempotencyKeyConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=IDEMPOTENCY_KEY_CONFLICT_ERROR)


@router.get(
    "/{payment_id}",
    response_model=PaymentOutputDTO,
    name="get_payment",
    status_code=status.HTTP_200_OK,
    responses={status.HTTP_404_NOT_FOUND: {"model": DetailErrorSchema}},
)
@inject
async def get_payment(
    payment_id: UUID,
    service: Annotated[AbstractGetPaymentService, Depends(Provide[Container.get_payment_service])],
) -> PaymentOutputDTO:
    try:
        return await service.process(payment_id)
    except PaymentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PAYMENT_NOT_FOUND_ERROR)
