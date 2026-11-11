import base64
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", "docker-compose.yaml", "-f", "docker-compose.e2e.yaml"]
API_URL = os.environ.get("E2E_API_URL", "http://localhost:8000")
RECEIVER_URL = os.environ.get("E2E_RECEIVER_URL", "http://localhost:8099")
RABBIT_URL = os.environ.get("E2E_RABBIT_MANAGEMENT_URL", "http://localhost:15672")
# Адрес приёмника, как его видят контейнеры сервиса.
RECEIVER_INTERNAL_URL = "http://webhook-receiver:8099"
API_KEY = "local-api-key"
WEBHOOK_SECRET = "whsec_ZTJlLXRlc3Qtc2VjcmV0LTAxMjM0NTY3ODlhYmNkZWY="
WORKERS = ("consumer", "outbox-relay")


def wait_until(condition: Callable[[], Any], timeout: float = 60, message: str = "condition is not met") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if result := condition():
                return result
        except httpx.HTTPError:
            pass
        assert time.monotonic() < deadline, message
        time.sleep(0.5)


class Stack:
    """Поднятое docker-compose окружение и всё, чем тест может на него посмотреть и подействовать."""

    def __init__(self) -> None:
        # trust_env=False: запросы к localhost не должны уходить в системный прокси.
        self.api = httpx.Client(base_url=API_URL, headers={"X-API-Key": API_KEY}, timeout=30, trust_env=False)
        self.receiver = httpx.Client(base_url=RECEIVER_URL, timeout=10, trust_env=False)
        self.rabbit = httpx.Client(base_url=RABBIT_URL, auth=("guest", "guest"), timeout=10, trust_env=False)

    def compose(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            [*COMPOSE, *args],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
            check=False,
        )
        assert not check or result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr

    def health(self, *services: str) -> dict[str, str]:
        output = self.compose("ps", "--all", "--format", "{{.Service}}={{.Health}}", *services)
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def wait_healthy(self, *services: str, timeout: float = 180) -> None:
        wait_until(
            lambda: set(self.health(*services).values()) == {"healthy"},
            timeout=timeout,
            message=f"services are not healthy: {self.health(*services)}",
        )

    def create_payment(self, mode: str = "ok", key: str | None = None, **body: Any) -> httpx.Response:
        payload = {
            "amount": "100.00",
            "currency": "RUB",
            "description": "e2e",
            "metadata": {"order_id": 42},
            "webhook_url": f"{RECEIVER_INTERNAL_URL}/hooks/{mode}",
        }
        return self.api.post(
            "/api/v1/payments",
            json=payload | body,
            headers={"Idempotency-Key": key or str(uuid.uuid4())},
        )

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        response = self.api.get(f"/api/v1/payments/{payment_id}")
        response.raise_for_status()
        return dict(response.json())

    def wait_delivered(self, payment_id: str, timeout: float = 60) -> dict[str, Any]:
        def delivered() -> dict[str, Any] | None:
            payment = self.get_payment(payment_id)
            return payment if payment["webhook_delivered_at"] else None

        return dict(wait_until(delivered, timeout=timeout, message=f"webhook for {payment_id} is not delivered"))

    def webhooks(self, payment_id: str) -> list[dict[str, Any]]:
        received = self.receiver.get("/requests").json()
        for request in received:
            request["raw"] = base64.b64decode(request["raw"])
            request["body"] = json.loads(request["raw"])
        return [request for request in received if request["headers"].get("webhook-id") == payment_id]

    def dead_letters(self) -> list[str]:
        """Идентификаторы платежей, чьи сообщения лежат в DLQ. Читаем сами сообщения: счётчики API запаздывают."""
        response = self.rabbit.post(
            "/api/queues/%2F/payments.new.dlq/get",
            json={"count": 1000, "ackmode": "ack_requeue_true", "encoding": "auto"},
        )
        response.raise_for_status()
        return [json.loads(message["payload"]).get("payment_id") for message in response.json()]

    def sql(self, query: str) -> str:
        return self.compose("exec", "-T", "db", "psql", "-U", "postgres", "-d", "payments", "-Atc", query).strip()


@pytest.fixture(scope="session")
def stack() -> Stack:
    stack = Stack()
    wait_until(lambda: stack.api.get("/healthcheck").status_code == 200, message="API is not available")
    wait_until(lambda: stack.receiver.get("/").status_code == 200, message="webhook receiver is not available")
    stack.wait_healthy(*WORKERS)
    return stack


@pytest.fixture(autouse=True)
def _restore_stack(stack: Stack) -> Iterator[None]:
    """После теста, который что-то остановил, возвращает окружение в рабочее состояние."""
    yield
    stack.compose("up", "-d", "--no-recreate")
    wait_until(lambda: stack.api.get("/healthcheck").status_code == 200, timeout=120, message="API did not recover")
    stack.wait_healthy(*WORKERS)
