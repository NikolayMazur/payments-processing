"""Проверки на свойствах: инварианты должны держаться на любых входных данных, а не на подобранных примерах."""

from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.app_layer.interfaces.services.payments.dto import CreatePaymentInputDTO
from app.app_layer.services.payments.create_payment_service import CreatePaymentService
from app.config import RabbitSettings
from app.domain.payments.enums import CurrencyEnum
from app.infra.rabbit.retry import RetryService
from app.utils import mask_url

amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999999999999999.99"), places=2)
json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**53), 2**53) | st.text(max_size=20),
    lambda children: st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3),
    max_leaves=8,
)
metadata = st.dictionaries(st.text(max_size=8), json_values, max_size=4)
safe_text = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=12)


def build_input(amount: Decimal, meta: dict[str, Any], **kwargs: Any) -> CreatePaymentInputDTO:
    data = {
        "idempotency_key": "key",
        "amount": amount,
        "currency": CurrencyEnum.RUB,
        "description": "d",
        "metadata": meta,
        "webhook_url": "https://example.com/hook",
    }
    return CreatePaymentInputDTO.model_validate(data | kwargs)


def request_hash(data: CreatePaymentInputDTO) -> str:
    return CreatePaymentService._build_request_hash(data)


@given(amount=amounts, meta=metadata, extra_zeros=st.integers(0, 6))
def test_same_amount_in_other_notation_is_same_request(amount: Decimal, meta: dict[str, Any], extra_zeros: int) -> None:
    other_notation = Decimal(f"{amount}{'0' * extra_zeros}")

    assert request_hash(build_input(amount, meta)) == request_hash(build_input(other_notation, meta))


@given(amount=amounts, meta=metadata, data=st.data())
def test_metadata_key_order_does_not_matter(amount: Decimal, meta: dict[str, Any], data: st.DataObject) -> None:
    shuffled = dict(data.draw(st.permutations(list(meta.items()))))

    assert request_hash(build_input(amount, meta)) == request_hash(build_input(amount, shuffled))


@given(amount=amounts, numbers=st.lists(st.integers(-(2**53), 2**53), min_size=1, max_size=5))
def test_integer_written_as_float_is_same_request(amount: Decimal, numbers: list[int]) -> None:
    as_integers = {"values": numbers, "nested": {"first": numbers[0]}}
    as_floats = {"values": [float(n) for n in numbers], "nested": {"first": float(numbers[0])}}

    assert request_hash(build_input(amount, as_integers)) == request_hash(build_input(amount, as_floats))


@given(amount=amounts, meta=metadata)
def test_any_changed_field_is_other_request(amount: Decimal, meta: dict[str, Any]) -> None:
    original = request_hash(build_input(amount, meta))

    changed = [
        build_input(amount + Decimal("0.01"), meta),
        build_input(amount, meta, currency=CurrencyEnum.USD),
        build_input(amount, meta, description="other"),
        build_input(amount, meta | {"__extra__": 1}),
        build_input(amount, meta, webhook_url="https://example.com/other"),
    ]

    assert original not in {request_hash(data) for data in changed}


@given(max_attempts=st.integers(1, 12), base_delay=st.integers(1, 30))
def test_message_is_processed_exactly_max_attempts_times(max_attempts: int, base_delay: int) -> None:
    service = RetryService(RabbitSettings(MAX_ATTEMPTS=max_attempts, RETRY_BASE_DELAY_SEC=base_delay))
    headers: dict[str, Any] = {}
    attempts, delays = 1, []
    while not service.has_exhausted_attempts(retry_attempt := service.extract_retry_attempt(headers)):
        delays.append(service.get_delay_sec(retry_attempt))
        headers = service.build_retry_headers(headers, retry_attempt)
        attempts += 1

    assert attempts == max_attempts
    assert len(delays) == service.retries_qty
    # Задержки растут вдвое, и на каждую есть своя очередь.
    assert delays == [base_delay * 2**n for n in range(len(delays))]
    assert len({service.get_retry_queue_name(n) for n in range(len(delays))}) == len(delays)


@settings(max_examples=200)
@given(user=safe_text, password=safe_text, host=safe_text, path=safe_text, token=safe_text, port=st.integers(1, 65535))
def test_masked_url_never_contains_credentials(
    user: str,
    password: str,
    host: str,
    path: str,
    token: str,
    port: int,
) -> None:
    masked = mask_url(f"https://{user}:SECRET{password}@{host}.example:{port}/{path}?token=SECRET{token}#SECRETfrag")

    assert masked == f"https://{host}.example:{port}/{path}"
    assert "SECRET" not in masked
