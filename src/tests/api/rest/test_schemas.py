from typing import Any

import pytest
from pydantic import ValidationError

from app.api.rest.v1.schemas import CreatePaymentRequestSchema, find_json_problem, find_text_problem
from app.config import config

VALID: dict[str, Any] = {"amount": "10.00", "currency": "RUB", "webhook_url": "https://example.com/hook"}
LONE_SURROGATE = "\ud800"


def nested(depth: int) -> Any:
    value: Any = "leaf"
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", None),
        ("кириллица и 🙂", None),
        ("a\x00b", "must not contain NUL character"),
        (f"a{LONE_SURROGATE}b", "must be valid Unicode text"),
    ],
)
def test_find_text_problem(value: str, expected: str | None) -> None:
    assert find_text_problem(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"key": "value", "n": 1, "f": 1.5, "b": True, "none": None, "list": [1, {"x": "y"}]}, None),
        ({"key": "a\x00b"}, "must not contain NUL character"),
        ({"a\x00b": 1}, "must not contain NUL character"),
        ({"list": ["ok", {"deep": "a\x00b"}]}, "must not contain NUL character"),
        ({"key": LONE_SURROGATE}, "must be valid Unicode text"),
        ({LONE_SURROGATE: 1}, "must be valid Unicode text"),
        ({"a": float("nan")}, "must not contain NaN or Infinity"),
        ({"a": [float("inf")]}, "must not contain NaN or Infinity"),
        ({"a": {"b": float("-inf")}}, "must not contain NaN or Infinity"),
        (42, None),
        (None, None),
    ],
)
def test_find_json_problem_looks_into_nested_values(value: Any, expected: str | None) -> None:
    assert find_json_problem(value, max_depth=32) == expected


def test_nesting_at_limit_accepted_and_one_level_deeper_rejected() -> None:
    assert find_json_problem(nested(5), max_depth=5) is None
    assert find_json_problem(nested(6), max_depth=5) == "must not be nested deeper than 5 levels"


def test_extremely_deep_nesting_does_not_overflow_stack() -> None:
    # Рекурсивный обход упал бы здесь с RecursionError.
    assert find_json_problem(nested(100_000), max_depth=32) == "must not be nested deeper than 32 levels"


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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("description", "a\x00b", "description must not contain NUL character"),
        # Одиночный суррогат в строковом поле pydantic отклоняет сам, раньше нашей проверки.
        ("description", LONE_SURROGATE, "unicode"),
        ("metadata", {"k": "a\x00b"}, "metadata must not contain NUL character"),
        ("metadata", {"k": float("nan")}, "metadata must not contain NaN or Infinity"),
        ("metadata", {"k": nested(config.API.MAX_METADATA_DEPTH)}, "metadata must not be nested deeper"),
    ],
)
def test_unstorable_values_rejected_with_clear_message(field: str, value: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CreatePaymentRequestSchema(**VALID, **{field: value})


@pytest.mark.parametrize("amount", ["1", "0.01", "1500.00", "10.5", "9999999999999999.99", 10, 0.1, 1500.5])
def test_amount_accepted_as_documented_string_or_number(amount: Any) -> None:
    assert CreatePaymentRequestSchema(**VALID | {"amount": amount})


@pytest.mark.parametrize(
    "amount",
    ["+5", " 5", "5 ", ".5", "5.", "1_000", "1e2", "1E-2", "١٢٣", "NaN", "Infinity", "", "0", "0.00", "-1", "1.234"],
)
def test_amount_string_in_other_notation_rejected(amount: str) -> None:
    with pytest.raises(ValidationError):
        CreatePaymentRequestSchema(**VALID | {"amount": amount})


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), 0, -1, 0.001, True])
def test_amount_number_out_of_range_rejected(amount: Any) -> None:
    with pytest.raises(ValidationError):
        CreatePaymentRequestSchema(**VALID | {"amount": amount})
