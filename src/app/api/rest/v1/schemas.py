import json
import math
import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, WithJsonSchema, field_validator

from app.config import config
from app.domain.payments.enums import CurrencyEnum

NUL = "\x00"

# Только ASCII-цифры: \d в Python пропустил бы и цифры других письменностей.
AMOUNT_PATTERN = r"^(?!0*(\.0*)?$)[0-9]{1,16}(\.[0-9]{1,2})?$"
AMOUNT_RE = re.compile(AMOUNT_PATTERN)
AMOUNT_ERROR = "amount must be a positive number with up to 2 decimal places, for example 1500.00"

# В схеме API сумма описана строкой: только так в JSON можно точно задать число знаков после запятой.
# Сервис принимает и JSON-число, но для денег строка надёжнее.
AMOUNT_JSON_SCHEMA = {
    "type": "string",
    "pattern": AMOUNT_PATTERN,
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


def find_text_problem(value: str) -> str | None:
    """Ищет в строке то, что PostgreSQL не сохранит: нулевой символ и одиночные суррогаты."""
    if NUL in value:
        return "must not contain NUL character"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "must be valid Unicode text"
    return None


def find_json_problem(value: Any, max_depth: int) -> str | None:
    """Проверяет значение, пришедшее из JSON, на то, что его можно сохранить в jsonb.

    Обход без рекурсии: глубина вложенности задаётся клиентом, и рекурсивный обход упёрся бы в предел стека.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, str):
            if problem := find_text_problem(item):
                return problem
        elif isinstance(item, float):
            # Python разбирает литералы NaN и Infinity и превращает 1e400 в бесконечность, а jsonb их не принимает.
            if not math.isfinite(item):
                return "must not contain NaN or Infinity"
        elif isinstance(item, dict | list):
            if depth > max_depth:
                return f"must not be nested deeper than {max_depth} levels"
            children = [*item.keys(), *item.values()] if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return None


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

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount_format(cls, value: Any) -> Any:
        # Строка принимается только в том виде, который обещает схема API. Без этой проверки
        # Decimal разобрал бы и «+5», и « 5», и «1_000», и цифры других письменностей.
        if isinstance(value, str) and not AMOUNT_RE.fullmatch(value):
            raise ValueError(AMOUNT_ERROR)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(AMOUNT_ERROR)
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if problem := find_text_problem(value):
            raise ValueError(f"description {problem}")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if problem := find_json_problem(value, max_depth=config.API.MAX_METADATA_DEPTH):
            raise ValueError(f"metadata {problem}")
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        if size > config.API.MAX_METADATA_BYTES:
            raise ValueError(f"metadata is too large: {size} bytes, limit is {config.API.MAX_METADATA_BYTES}")
        return value
