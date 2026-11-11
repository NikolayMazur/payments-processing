from typing import Any

import pytest
from pydantic import ValidationError

from app.api.rest.v1.schemas import CreatePaymentRequestSchema, contains_nul
from app.config import config

VALID: dict[str, Any] = {"amount": "10.00", "currency": "RUB", "webhook_url": "https://example.com/hook"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", False),
        ("a\x00b", True),
        ({"key": "value"}, False),
        ({"key": "a\x00b"}, True),
        ({"a\x00b": 1}, True),
        ({"list": ["ok", {"deep": "a\x00b"}]}, True),
        ({"list": ["ok", {"deep": ["fine", 1, None, True]}]}, False),
        ([["a\x00"]], True),
        (42, False),
        (None, False),
    ],
)
def test_contains_nul_looks_into_nested_values(value: Any, expected: bool) -> None:
    assert contains_nul(value) is expected


def test_metadata_at_size_limit_accepted_and_one_byte_over_rejected() -> None:
    overhead = len('{"k":""}')
    at_limit = {"k": "x" * (config.API.MAX_METADATA_BYTES - overhead)}

    assert CreatePaymentRequestSchema(**VALID, metadata=at_limit)
    with pytest.raises(ValidationError, match="metadata is too large"):
        CreatePaymentRequestSchema(**VALID, metadata={"k": at_limit["k"] + "x"})


def test_metadata_size_is_counted_in_bytes_not_characters() -> None:
    # Кириллица в UTF-8 занимает два байта на букву.
    half = {"k": "я" * (config.API.MAX_METADATA_BYTES // 2)}

    with pytest.raises(ValidationError, match="metadata is too large"):
        CreatePaymentRequestSchema(**VALID, metadata=half)


@pytest.mark.parametrize("field", ["description", "metadata"])
def test_nul_character_rejected_with_clear_message(field: str) -> None:
    value: Any = "a\x00b" if field == "description" else {"k": "a\x00b"}

    with pytest.raises(ValidationError, match=f"{field} must not contain NUL character"):
        CreatePaymentRequestSchema(**VALID, **{field: value})
