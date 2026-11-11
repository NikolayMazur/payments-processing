from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum


class Payment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    amount: Decimal
    currency: CurrencyEnum
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: PaymentStatusEnum = PaymentStatusEnum.PENDING
    idempotency_key: str
    request_hash: str
    webhook_url: str
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None

    @property
    def is_pending(self) -> bool:
        return self.status is PaymentStatusEnum.PENDING
