"""Нагрузочный тест: make loadtest

Каждый виртуальный клиент создаёт платёж и ждёт, пока сервис проведёт его и доставит вебхук.
Прогон считается проваленным, если нарушен хотя бы один порог:
    LOAD_MAX_FAIL_RATIO          доля неуспешных запросов, по умолчанию 0
    LOAD_MAX_CREATE_P95_MS       95-й перцентиль времени приёма платежа, по умолчанию 300 мс
    LOAD_MAX_TIME_TO_RESULT_P95_MS  95-й перцентиль времени от создания до доставленного вебхука, по умолчанию 20 с
"""

import os
import time
import uuid
from typing import Any

from locust import HttpUser, between, events, task
from locust.env import Environment

API_KEY = os.environ.get("LOAD_API_KEY", "local-api-key")
WEBHOOK_URL = os.environ.get("LOAD_WEBHOOK_URL", "http://webhook-receiver:8099/hooks/ok")
MAX_FAIL_RATIO = float(os.environ.get("LOAD_MAX_FAIL_RATIO", "0"))
MAX_CREATE_P95_MS = float(os.environ.get("LOAD_MAX_CREATE_P95_MS", "300"))
MAX_TIME_TO_RESULT_P95_MS = float(os.environ.get("LOAD_MAX_TIME_TO_RESULT_P95_MS", "20000"))
RESULT_TIMEOUT_SEC = float(os.environ.get("LOAD_RESULT_TIMEOUT_SEC", "120"))

CREATE_NAME = "POST /api/v1/payments"
POLL_NAME = "GET /api/v1/payments/{payment_id}"
FLOW_NAME = "создание → доставленный вебхук"


class PaymentClient(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.client.headers["X-API-Key"] = API_KEY

    @task
    def pay_and_wait_for_result(self) -> None:
        started_at = time.perf_counter()
        body = {"amount": "100.00", "currency": "RUB", "webhook_url": WEBHOOK_URL, "metadata": {"load": True}}
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        response = self.client.post("/api/v1/payments", json=body, headers=headers, name=CREATE_NAME)
        if response.status_code != 202:
            return
        payment_id = response.json()["payment_id"]

        error: Exception | None = None
        while not self._is_delivered(payment_id):
            if time.perf_counter() - started_at > RESULT_TIMEOUT_SEC:
                error = TimeoutError(f"payment {payment_id} is not delivered in {RESULT_TIMEOUT_SEC} sec")
                break
            time.sleep(0.5)
        # Весь путь платежа записывается отдельной строкой отчёта, по ней считается главный порог.
        self.environment.events.request.fire(
            request_type="FLOW",
            name=FLOW_NAME,
            response_time=(time.perf_counter() - started_at) * 1000,
            response_length=0,
            exception=error,
            context={},
        )

    def _is_delivered(self, payment_id: str) -> bool:
        response = self.client.get(f"/api/v1/payments/{payment_id}", name=POLL_NAME)
        return response.status_code == 200 and response.json()["webhook_delivered_at"] is not None


@events.quitting.add_listener
def check_thresholds(environment: Environment, **_: Any) -> None:
    stats = environment.stats
    create_p95 = stats.get(CREATE_NAME, "POST").get_response_time_percentile(0.95)
    flow_p95 = stats.get(FLOW_NAME, "FLOW").get_response_time_percentile(0.95)
    violations = []
    if stats.get(FLOW_NAME, "FLOW").num_requests == 0:
        violations.append("ни один платёж не дошёл до доставленного вебхука")
    if stats.total.fail_ratio > MAX_FAIL_RATIO:
        violations.append(f"доля ошибок {stats.total.fail_ratio:.2%} больше {MAX_FAIL_RATIO:.2%}")
    if create_p95 > MAX_CREATE_P95_MS:
        violations.append(f"p95 приёма платежа {create_p95:.0f} мс больше {MAX_CREATE_P95_MS:.0f} мс")
    if flow_p95 > MAX_TIME_TO_RESULT_P95_MS:
        violations.append(f"p95 времени до результата {flow_p95:.0f} мс больше {MAX_TIME_TO_RESULT_P95_MS:.0f} мс")

    print(f"\np95 приёма платежа: {create_p95:.0f} мс, p95 времени до результата: {flow_p95:.0f} мс")  # noqa: T201
    if violations:
        print("Пороги нарушены: " + "; ".join(violations))  # noqa: T201
        environment.process_exit_code = 1
    else:
        print("Все пороги выдержаны")  # noqa: T201
