"""Схема API как контракт: случайная правка эндпоинта или DTO меняет её и роняет тест.

Если изменение намеренное, снимок обновляется командой
    UPDATE_SNAPSHOTS=1 make test target=tests/api/rest/test_openapi.py
"""

import json
import os
import re
from pathlib import Path

from app.api.rest.main import app
from app.api.rest.v1.schemas import CreatePaymentRequestSchema

SNAPSHOT = Path(__file__).parent / "openapi.json"


def test_openapi_schema_matches_snapshot() -> None:
    schema = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(schema)

    assert schema == SNAPSHOT.read_text(), "Схема API изменилась. Если так задумано, обновите снимок."


def test_documented_contract_of_payments_api() -> None:
    paths = app.openapi()["paths"]

    create = paths["/api/v1/payments"]["post"]
    assert set(create["responses"]) == {"202", "400", "401", "409", "422", "503"}
    assert {param["name"] for param in create["parameters"] if param["required"]} == {"Idempotency-Key"}
    get = paths["/api/v1/payments/{payment_id}"]["get"]
    assert set(get["responses"]) == {"200", "401", "404", "422", "503"}
    assert "/metrics" not in paths


def test_documented_amount_format_is_exactly_what_api_accepts() -> None:
    """Всё, что схема называет корректной суммой, сервис принимает, а явный мусор схема не пропускает."""
    schema = app.openapi()["components"]["schemas"]["CreatePaymentRequestSchema"]["properties"]["amount"]
    pattern = re.compile(schema["pattern"])

    for valid in ("1", "0.01", "1500.00", "10.5", "9999999999999999.99", "007.10"):
        assert pattern.fullmatch(valid), valid
        assert CreatePaymentRequestSchema(amount=valid, currency="RUB", webhook_url="https://example.com/h")
    for invalid in ("0", "0.00", "-1", "1.234", "abc", "", "1e2", " 5", "NaN", "12345678901234567.00"):
        assert not pattern.fullmatch(invalid), invalid
