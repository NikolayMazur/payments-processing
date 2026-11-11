import json
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.config import config
from app.domain.payments.enums import CurrencyEnum


class CreatePaymentRequestSchema(BaseModel):
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2, examples=["1500.00"])]
    currency: CurrencyEnum
    description: Annotated[str, Field(max_length=1024)] = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: HttpUrl

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        if size > config.API.MAX_METADATA_BYTES:
            raise ValueError(f"metadata is too large: {size} bytes, limit is {config.API.MAX_METADATA_BYTES}")
        return value
