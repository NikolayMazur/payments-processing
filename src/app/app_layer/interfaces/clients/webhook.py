from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum


class WebhookDeliveryError(Exception):
    """Получатель вебхука недоступен или ответил не 2xx"""


class PaymentWebhookDTO(BaseModel):
    payment_id: UUID
    status: PaymentStatusEnum
    amount: Decimal
    currency: CurrencyEnum
    description: str
    metadata: dict[str, Any]
    failure_reason: str | None
    created_at: datetime
    processed_at: datetime | None


class AbstractWebhookClient(ABC):
    @abstractmethod
    async def send(self, url: str, data: PaymentWebhookDTO) -> None:
        raise NotImplementedError
