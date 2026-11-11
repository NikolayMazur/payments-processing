import json
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, WithJsonSchema, field_validator

from app.config import config
from app.domain.payments.enums import CurrencyEnum

NUL = "\x00"

# В схеме API сумма описана строкой: только так в JSON можно точно задать число знаков после запятой.
# Сервис принимает и JSON-число, но для денег строка надёжнее.
AMOUNT_JSON_SCHEMA = {
    "type": "string",
    "pattern": r"^(?!0*(\.0*)?$)\d{1,16}(\.\d{1,2})?$",
    "description": "Сумма больше нуля, не более двух знаков после запятой",
    "examples": ["1500.00"],
}
WEBHOOK_URL_JSON_SCHEMA = {
    "type": "string",
    "pattern": r"^https?://[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?){0,4}(:[1-9][0-9]{1,3})?(/[A-Za-z0-9._~-]{0,40}){0,5}$",
    "maxLength": 2083,
    "description": "Адрес http или https, на который придёт уведомление о результате",
    "examples": ["https://client.example.com/webhooks/payments"],
}


def contains_nul(value: Any) -> bool:
    if isinstance(value, str):
        return NUL in value
    if isinstance(value, dict):
        return any(contains_nul(key) or contains_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_nul(item) for item in value)
    return False


class CreatePaymentRequestSchema(BaseModel):
    amount: Annotated[
        Decimal,
        Field(gt=0, max_digits=18, decimal_places=2),
        WithJsonSchema(AMOUNT_JSON_SCHEMA),
    ]
    currency: CurrencyEnum
    description: Annotated[str, Field(max_length=1024, json_schema_extra={"pattern": r"^[^\u0000]*$"})] = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: Annotated[HttpUrl, WithJsonSchema(WEBHOOK_URL_JSON_SCHEMA)]

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        # PostgreSQL не хранит нулевой символ ни в text, ни в jsonb: без проверки запрос падал бы с 500.
        if contains_nul(value):
            raise ValueError("description must not contain NUL character")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if contains_nul(value):
            raise ValueError("metadata must not contain NUL character")
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        if size > config.API.MAX_METADATA_BYTES:
            raise ValueError(f"metadata is too large: {size} bytes, limit is {config.API.MAX_METADATA_BYTES}")
        return value
