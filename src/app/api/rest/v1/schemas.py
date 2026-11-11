from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl

from app.domain.payments.enums import CurrencyEnum


class CreatePaymentRequestSchema(BaseModel):
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2, examples=["1500.00"])]
    currency: CurrencyEnum
    description: Annotated[str, Field(max_length=1024)] = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: HttpUrl
