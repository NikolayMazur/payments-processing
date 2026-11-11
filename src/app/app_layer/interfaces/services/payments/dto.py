from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum


class CreatePaymentInputDTO(BaseModel):
    idempotency_key: str
    amount: Decimal
    currency: CurrencyEnum
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str


class CreatePaymentOutputDTO(BaseModel):
    payment_id: UUID
    status: PaymentStatusEnum
    created_at: datetime


class PaymentOutputDTO(BaseModel):
    payment_id: UUID
    amount: Decimal
    currency: CurrencyEnum
    description: str
    metadata: dict[str, Any]
    status: PaymentStatusEnum
    idempotency_key: str
    webhook_url: str
    failure_reason: str | None
    created_at: datetime
    processed_at: datetime | None
    webhook_attempts: int
    webhook_delivered_at: datetime | None


class ProcessPaymentInputDTO(BaseModel):
    payment_id: UUID
