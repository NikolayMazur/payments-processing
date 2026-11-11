from typing import Any

import pytest

from app.config import RabbitSettings
from app.infra.rabbit.retry import RetryService


@pytest.fixture()
def service() -> RetryService:
    return RetryService(RabbitSettings(MAX_ATTEMPTS=3, RETRY_BASE_DELAY_SEC=2))


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, 0),
        ({"x-retry-attempt": 1}, 1),
        ({"x-retry-attempt": "2"}, 2),
        ({"x-retry-attempt": "broken"}, 0),
        ({"x-retry-attempt": None}, 0),
        ({"x-retry-attempt": -4}, 0),
    ],
)
def test_extract_retry_attempt(service: RetryService, headers: dict[str, Any], expected: int) -> None:
    assert service.extract_retry_attempt(headers) == expected


@pytest.mark.parametrize(("retry_attempt", "expected"), [(0, False), (1, False), (2, True), (3, True)])
def test_attempts_exhausted_after_third_processing(service: RetryService, retry_attempt: int, expected: bool) -> None:
    assert service.has_exhausted_attempts(retry_attempt) is expected


def test_delay_grows_exponentially(service: RetryService) -> None:
    assert [service.get_delay_sec(retry_attempt) for retry_attempt in range(3)] == [2, 4, 8]


def test_retry_headers_keep_original_and_increment_attempt(service: RetryService) -> None:
    headers = service.build_retry_headers({"trace": "abc", "x-retry-attempt": 1}, retry_attempt=1)

    assert headers == {"trace": "abc", "x-retry-attempt": 2}


def test_retry_queue_per_attempt(service: RetryService) -> None:
    assert service.retries_qty == 2
    assert service.get_retry_queue_name(0) == "payments.new.retry.1"
    assert service.get_retry_queue_name(1) == "payments.new.retry.2"
