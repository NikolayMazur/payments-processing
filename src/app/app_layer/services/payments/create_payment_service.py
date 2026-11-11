import hashlib
import json
from decimal import Decimal
from logging import getLogger

from app.app_layer.interfaces.services.payments.dto import CreatePaymentInputDTO, CreatePaymentOutputDTO
from app.app_layer.interfaces.services.payments.exceptions import IdempotencyKeyConflictError
from app.app_layer.interfaces.services.payments.services import AbstractCreatePaymentService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.config import RabbitSettings
from app.domain.outbox.entity import Outbox
from app.domain.payments.entity import Payment
from app.domain.payments.events import PaymentCreatedEvent
from app.utils import utc_now

logger = getLogger(__name__)

AMOUNT_QUANT = Decimal("0.01")


class CreatePaymentService(AbstractCreatePaymentService):
    def __init__(self, uow: AbcUnitOfWork, rabbit_settings: RabbitSettings) -> None:
        self._uow = uow
        self._rabbit_settings = rabbit_settings

    async def process(self, data: CreatePaymentInputDTO) -> CreatePaymentOutputDTO:
        now = utc_now()
        payment = Payment(
            amount=data.amount.quantize(AMOUNT_QUANT),
            currency=data.currency,
            description=data.description,
            metadata=data.metadata,
            idempotency_key=data.idempotency_key,
            request_hash=self._build_request_hash(data),
            webhook_url=data.webhook_url,
            created_at=now,
            updated_at=now,
        )
        async with self._uow:
            # Платёж и событие пишутся одной транзакцией: либо появятся оба, либо ни одного.
            if await self._uow.payment.create_if_absent(payment):
                await self._uow.outbox.create_from_entity(self._build_outbox(payment))
                return self._build_response(payment)
            existing = await self._uow.payment.get_by_idempotency_key(data.idempotency_key)

        if existing is None or existing.request_hash != payment.request_hash:
            raise IdempotencyKeyConflictError
        logger.info("Payment %s returned by idempotency key", existing.id)
        return self._build_response(existing)

    @staticmethod
    def _build_request_hash(data: CreatePaymentInputDTO) -> str:
        canonical = json.dumps(
            {
                "amount": str(data.amount.quantize(AMOUNT_QUANT)),
                "currency": data.currency.value,
                "description": data.description,
                "metadata": data.metadata,
                "webhook_url": data.webhook_url,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _build_outbox(self, payment: Payment) -> Outbox:
        return Outbox(
            exchange=self._rabbit_settings.EXCHANGE,
            routing_key=self._rabbit_settings.QUEUE,
            event_key=str(payment.id),
            payload=PaymentCreatedEvent(payment_id=payment.id).model_dump(mode="json"),
            created_at=payment.created_at,
        )

    @staticmethod
    def _build_response(payment: Payment) -> CreatePaymentOutputDTO:
        return CreatePaymentOutputDTO(payment_id=payment.id, status=payment.status, created_at=payment.created_at)
