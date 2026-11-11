from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domain.outbox.entity import Outbox
from app.domain.payments.entity import Payment
from app.domain.payments.enums import CurrencyEnum
from app.domain.payments.events import PaymentCreatedEvent
from app.utils import utc_now
from tests.constants import TEST_WEBHOOK_URL
from tests.environment.unit_of_work import TestUow


class PaymentFactory:
    def __init__(self, uow: TestUow) -> None:
        self.uow = uow

    @classmethod
    def base_generate(cls) -> dict[str, Any]:
        now = utc_now()
        return {
            "amount": Decimal("100.50"),
            "currency": CurrencyEnum.RUB,
            "description": "Заказ 42",
            "metadata": {"order_id": 42},
            "idempotency_key": str(uuid4()),
            "request_hash": "0" * 64,
            "webhook_url": TEST_WEBHOOK_URL,
            "created_at": now,
            "updated_at": now,
        }

    async def payment(self, **kwargs: Any) -> Payment:
        entity = Payment(**(self.base_generate() | kwargs))
        async with self.uow:
            await self.uow.payment.create_if_absent(entity)
        return entity


class OutboxFactory:
    def __init__(self, uow: TestUow) -> None:
        self.uow = uow
        self._created = 0

    async def outbox(self, **kwargs: Any) -> Outbox:
        payment_id = uuid4()
        # Записи читаются в порядке created_at, поэтому каждой следующей даём время чуть позже.
        self._created += 1
        data = {
            "exchange": "payments",
            "routing_key": "payments.new",
            "event_key": str(payment_id),
            "payload": PaymentCreatedEvent(payment_id=payment_id).model_dump(mode="json"),
            "created_at": utc_now() + timedelta(milliseconds=self._created),
        }
        entity = Outbox(**(data | kwargs))
        async with self.uow:
            await self.uow.outbox.create_from_entity(entity)
        return entity
