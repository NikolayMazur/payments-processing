"""Сквозные тесты на поднятом docker-compose окружении: make e2e

Сервис проверяется снаружи, как его видит клиент: HTTP-запросы на вход, вебхуки на выходе.
Сбои настоящие: контейнеры останавливаются, убиваются и перезапускаются.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from standardwebhooks import Webhook

from e2e.conftest import WEBHOOK_SECRET, WORKERS, Stack, wait_until


def test_all_services_are_healthy(stack: Stack) -> None:
    health = stack.health()

    assert {health[name] for name in ("api", "db", "rabbitmq", *WORKERS)} == {"healthy"}


def test_payment_is_processed_and_client_is_notified(stack: Stack) -> None:
    response = stack.create_payment()
    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "pending"

    payment = stack.wait_delivered(created["payment_id"])

    assert payment["status"] in {"succeeded", "failed"}
    assert payment["processed_at"] is not None
    assert payment["webhook_attempts"] == 1
    webhooks = stack.webhooks(created["payment_id"])
    assert len(webhooks) == 1
    # Подпись проверяет официальная библиотека получателя.
    body = Webhook(WEBHOOK_SECRET).verify(webhooks[0]["raw"], webhooks[0]["headers"])
    assert body["payment_id"] == created["payment_id"]
    assert body["status"] == payment["status"]
    assert body["amount"] == "100.00"
    assert body["metadata"] == {"order_id": 42}


def test_parallel_requests_with_same_key_create_one_payment(stack: Stack) -> None:
    key = str(uuid.uuid4())

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(lambda _: stack.create_payment(key=key), range(40)))

    assert {response.status_code for response in responses} == {202}
    payment_ids = {response.json()["payment_id"] for response in responses}
    assert len(payment_ids) == 1
    assert stack.create_payment(key=key, amount="1.00").status_code == 409
    payment_id = payment_ids.pop()
    stack.wait_delivered(payment_id)
    assert stack.sql(f"select count(*) from payments where idempotency_key = '{key}'") == "1"
    assert stack.sql(f"select count(*) from outbox where event_key = '{payment_id}'") == "1"
    # Одно событие, один вебхук: дублей нет ни в outbox, ни у получателя.
    assert len(stack.webhooks(payment_id)) == 1


def test_api_rejects_requests_without_key_and_with_bad_body(stack: Stack) -> None:
    assert stack.api.get("/api/v1/payments/x", headers={"X-API-Key": "wrong"}).status_code == 401
    assert stack.api.post("/api/v1/payments", json={}, headers={"X-API-Key": ""}).status_code == 401
    assert stack.api.post("/api/v1/payments", json={"amount": "1"}).status_code == 422
    assert stack.create_payment(amount="0").status_code == 422
    assert stack.api.get(f"/api/v1/payments/{uuid.uuid4()}").status_code == 404


def test_unstable_receiver_gets_webhook_on_third_attempt(stack: Stack) -> None:
    payment_id = stack.create_payment(mode="flaky").json()["payment_id"]

    payment = stack.wait_delivered(payment_id)

    webhooks = stack.webhooks(payment_id)
    assert [webhook["code"] for webhook in webhooks] == [503, 503, 200]
    assert payment["webhook_attempts"] == 3
    first_gap, second_gap = (webhooks[i + 1]["time"] - webhooks[i]["time"] for i in range(2))
    # Экспоненциальная задержка: 2 и 4 секунды.
    assert 1.8 <= first_gap < 3.5
    assert 3.8 <= second_gap < 5.5
    # Платёж между попытками не проводился заново: статус во всех уведомлениях один.
    assert len({webhook["body"]["status"] for webhook in webhooks}) == 1
    assert payment_id not in stack.dead_letters()


def test_undelivered_webhook_goes_to_dlq_and_can_be_requeued(stack: Stack) -> None:
    payment_id = stack.create_payment(mode="flaky5").json()["payment_id"]

    wait_until(lambda: payment_id in stack.dead_letters(), message="message is not in DLQ after three attempts")

    payment = stack.get_payment(payment_id)
    assert payment["webhook_attempts"] == 3
    assert payment["webhook_delivered_at"] is None
    # Итог платежа известен и доступен клиенту, хотя уведомление не дошло.
    assert payment["status"] in {"succeeded", "failed"}

    output = stack.compose("run", "--rm", "-T", "api", "python", "manage.py", "requeue-dlq")

    assert "Возвращено сообщений в обработку" in output
    delivered = stack.wait_delivered(payment_id)
    assert delivered["webhook_attempts"] == 5
    assert delivered["status"] == payment["status"]
    assert delivered["processed_at"] == payment["processed_at"]
    assert [webhook["code"] for webhook in stack.webhooks(payment_id)] == [503, 503, 503, 503, 200]
    assert payment_id not in stack.dead_letters()


def test_consumer_finishes_payment_before_graceful_stop(stack: Stack) -> None:
    payment_id = stack.create_payment().json()["payment_id"]
    time.sleep(1)
    assert stack.get_payment(payment_id)["status"] == "pending"

    stack.compose("stop", "consumer")

    # Консьюмера уже нет, а платёж обработан и клиент уведомлён: он успел закончить до выхода.
    payment = stack.get_payment(payment_id)
    assert payment["status"] != "pending"
    assert payment["webhook_delivered_at"] is not None
    assert len(stack.webhooks(payment_id)) == 1


def test_payments_of_killed_consumer_are_processed_after_restart(stack: Stack) -> None:
    payment_ids = [stack.create_payment().json()["payment_id"] for _ in range(5)]
    time.sleep(1)

    stack.compose("kill", "consumer")

    assert all(stack.get_payment(payment_id)["status"] == "pending" for payment_id in payment_ids)
    stack.compose("up", "-d", "--no-recreate", "consumer")
    for payment_id in payment_ids:
        stack.wait_delivered(payment_id, timeout=90)
    assert all(len(stack.webhooks(payment_id)) == 1 for payment_id in payment_ids)
    assert stack.sql("select count(*) from payments where status = 'pending'") == "0"


def test_event_waits_in_outbox_while_relay_is_down(stack: Stack) -> None:
    stack.compose("stop", "outbox-relay")
    payment_id = stack.create_payment().json()["payment_id"]
    time.sleep(5)

    assert stack.get_payment(payment_id)["status"] == "pending"
    assert stack.sql(f"select status from outbox where event_key = '{payment_id}'") == "PENDING"

    stack.compose("start", "outbox-relay")

    stack.wait_delivered(payment_id)
    assert stack.sql(f"select status from outbox where event_key = '{payment_id}'") == "SUCCESS"


def test_payment_accepted_while_broker_is_down_is_processed_later(stack: Stack) -> None:
    stack.compose("stop", "rabbitmq")

    response = stack.create_payment()

    # Приём платежа не зависит от брокера: событие лежит в outbox.
    assert response.status_code == 202
    payment_id = response.json()["payment_id"]
    wait_until(
        lambda: set(stack.health(*WORKERS).values()) == {"unhealthy"},
        timeout=120,
        message=f"workers did not become unhealthy without broker: {stack.health(*WORKERS)}",
    )
    assert stack.get_payment(payment_id)["status"] == "pending"

    stack.compose("start", "rabbitmq")

    stack.wait_delivered(payment_id, timeout=180)
    stack.wait_healthy(*WORKERS)


def test_service_recovers_after_database_restart(stack: Stack) -> None:
    before = stack.create_payment().json()["payment_id"]
    stack.compose("stop", "db")

    unavailable = stack.create_payment()

    assert unavailable.status_code == 503
    assert unavailable.headers["Retry-After"] == "5"
    assert stack.api.get("/healthcheck").status_code == 503

    stack.compose("start", "db")
    key = str(uuid.uuid4())
    response = wait_until(
        lambda: (lambda r: r if r.status_code == 202 else None)(stack.create_payment(key=key)),
        timeout=90,
        message="API did not recover after database restart",
    )

    stack.wait_delivered(response.json()["payment_id"], timeout=120)
    # Платёж, принятый до остановки БД, тоже доведён до конца.
    stack.wait_delivered(before, timeout=120)


def test_request_id_connects_api_and_consumer_logs(stack: Stack) -> None:
    request_id = f"e2e-{uuid.uuid4()}"
    response = stack.api.post(
        "/api/v1/payments",
        json={
            "amount": "5.00",
            "currency": "EUR",
            "webhook_url": "http://webhook-receiver:8099/hooks/ok?token=secret-token",
        },
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Request-ID": request_id},
    )
    assert response.headers["X-Request-ID"] == request_id
    stack.wait_delivered(response.json()["payment_id"])

    logs = stack.compose("logs", "--since", "2m", "api", "consumer", "outbox-relay")

    lines = [line for line in logs.splitlines() if request_id in line]
    assert any(line.startswith("api") for line in lines)
    assert any(line.startswith("consumer") for line in lines)
    assert "secret-token" not in logs


def test_metrics_reflect_processed_payments(stack: Stack) -> None:
    api_metrics = stack.api.get("/metrics").text
    script = "import urllib.request; print(urllib.request.urlopen('http://localhost:9000').read().decode())"
    consumer_metrics = stack.compose("exec", "-T", "consumer", "python", "-c", script)
    relay_metrics = stack.compose("exec", "-T", "outbox-relay", "python", "-c", script)

    assert 'http_requests_total{method="POST",path="/api/v1/payments",status="202"}' in api_metrics
    assert "payments_processed_total{" in consumer_metrics
    assert 'webhook_deliveries_total{result="delivered"}' in consumer_metrics
    assert "outbox_published_total" in relay_metrics
    assert "dead_letter_queue_messages" in relay_metrics
