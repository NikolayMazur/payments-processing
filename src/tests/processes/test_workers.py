"""Тесты на уровне процессов: настоящие консьюмер и relay, настоящие сигналы и обрывы соединений.

Проверяют то, что не видно изнутри одного процесса: запуск теми же командами, что в docker-compose,
штатную остановку по SIGTERM, восстановление после SIGKILL и после потери связи с брокером и БД.
"""

import asyncio
import urllib.request
from collections.abc import Callable
from datetime import timedelta
from subprocess import CompletedProcess

import pytest
from sqlalchemy import text
from standardwebhooks import Webhook

from app.app_layer.interfaces.services.payments.dto import CreatePaymentInputDTO
from app.app_layer.services.payments.create_payment_service import CreatePaymentService
from app.config import config
from app.containers import Container
from app.domain.outbox.enums import OutboxStatusEnum
from app.domain.payments.entity import Payment
from app.domain.payments.enums import CurrencyEnum, PaymentStatusEnum
from app.utils import utc_now
from tests.environment.unit_of_work import TestUow
from tests.factories import OutboxFactory, PaymentFactory
from tests.processes.conftest import WEBHOOK_SECRET, WebhookReceiver, Worker, wait_until
from tests.rabbit_utils import RabbitManagement

pytestmark = pytest.mark.integration

StartWorker = Callable[..., Worker]
RunCommand = Callable[..., CompletedProcess[str]]


def publish_payment_created(rabbit: RabbitManagement, payment: Payment) -> None:
    assert rabbit.publish("payments", "payments.new", {"payment_id": str(payment.id)}, {"x-request-id": "req-proc"})


def metrics_of(worker: Worker) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{worker.metrics_port}", timeout=5) as response:
        return str(response.read().decode())


async def get_payment(uow: TestUow, payment: Payment) -> Payment:
    async with uow:
        saved = await uow.payment.get_by_id(payment.id)
    assert saved is not None
    return saved


async def create_payment(uow: TestUow, webhook_url: str, key: str) -> None:
    service = CreatePaymentService(uow=uow, rabbit_settings=config.RABBIT)
    await service.process(
        CreatePaymentInputDTO(idempotency_key=key, amount="10.00", currency=CurrencyEnum.RUB, webhook_url=webhook_url),
    )


class TestConsumerProcess:
    async def test_consumer_processes_payment_and_stops_on_sigterm(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        webhook_receiver: WebhookReceiver,
        payment_factory: PaymentFactory,
        uow: TestUow,
    ) -> None:
        payment = await payment_factory.payment(webhook_url=webhook_receiver.url)
        consumer = start_worker("consumer")

        publish_payment_created(rabbit, payment)
        wait_until(lambda: webhook_receiver.requests, message="webhook is not received")

        saved = await get_payment(uow, payment)
        assert saved.status is PaymentStatusEnum.SUCCEEDED
        assert saved.webhook_attempts == 1
        request = webhook_receiver.requests[0]
        # Подпись настоящего процесса проверяет официальная библиотека получателя.
        assert Webhook(WEBHOOK_SECRET).verify(request["raw"], request["headers"])["payment_id"] == str(payment.id)
        metrics = metrics_of(consumer)
        assert 'payments_processed_total{status="succeeded"} 1.0' in metrics
        assert 'webhook_deliveries_total{result="delivered"} 1.0' in metrics

        assert consumer.terminate() == 0
        assert rabbit.peek("payments.new") == []

    async def test_sigterm_lets_consumer_finish_payment_in_progress(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        webhook_receiver: WebhookReceiver,
        payment_factory: PaymentFactory,
        uow: TestUow,
    ) -> None:
        payment = await payment_factory.payment(webhook_url=webhook_receiver.url)
        consumer = start_worker("consumer", GATEWAY__MIN_DELAY_SEC="2", GATEWAY__MAX_DELAY_SEC="2")
        publish_payment_created(rabbit, payment)
        await asyncio.sleep(0.7)
        assert (await get_payment(uow, payment)).status is PaymentStatusEnum.PENDING

        exit_code = consumer.terminate()

        assert exit_code == 0
        saved = await get_payment(uow, payment)
        assert saved.status is PaymentStatusEnum.SUCCEEDED
        assert saved.webhook_delivered_at is not None
        assert len(webhook_receiver.requests) == 1
        assert rabbit.peek("payments.new") == []

    async def test_message_of_killed_consumer_is_processed_by_next_one(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        webhook_receiver: WebhookReceiver,
        payment_factory: PaymentFactory,
        uow: TestUow,
    ) -> None:
        payment = await payment_factory.payment(webhook_url=webhook_receiver.url)
        consumer = start_worker("consumer", GATEWAY__MIN_DELAY_SEC="5", GATEWAY__MAX_DELAY_SEC="5")
        publish_payment_created(rabbit, payment)
        await asyncio.sleep(0.7)

        consumer.kill()

        # Убитый процесс не подтвердил сообщение, брокер вернул его в очередь.
        wait_until(lambda: rabbit.peek("payments.new"), message="message is not returned to the queue")
        assert (await get_payment(uow, payment)).status is PaymentStatusEnum.PENDING
        assert webhook_receiver.requests == []

        start_worker("consumer")
        wait_until(lambda: webhook_receiver.requests, message="webhook is not received after restart")

        assert (await get_payment(uow, payment)).status is PaymentStatusEnum.SUCCEEDED
        assert len(webhook_receiver.requests) == 1

    async def test_failed_webhook_goes_through_real_retries_to_dlq(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        webhook_receiver: WebhookReceiver,
        payment_factory: PaymentFactory,
        uow: TestUow,
    ) -> None:
        webhook_receiver.status_code = 500
        payment = await payment_factory.payment(webhook_url=webhook_receiver.url)
        consumer = start_worker("consumer")

        publish_payment_created(rabbit, payment)
        dead_letters = wait_until(lambda: rabbit.peek("payments.new.dlq"), message="message is not in DLQ")

        assert [letter["body"] for letter in dead_letters] == [{"payment_id": str(payment.id)}]
        assert dead_letters[0]["headers"]["x-retry-attempt"] == 2
        assert dead_letters[0]["headers"]["x-request-id"] == "req-proc"
        saved = await get_payment(uow, payment)
        assert saved.webhook_attempts == 3
        assert saved.webhook_delivered_at is None
        # Идентификатор уведомления один на все попытки.
        assert {request["headers"]["webhook-id"] for request in webhook_receiver.requests} == {str(payment.id)}
        assert 'payment_messages_dead_lettered_total{reason="attempts_exhausted"} 1.0' in metrics_of(consumer)

    async def test_consumer_survives_lost_broker_connection(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        webhook_receiver: WebhookReceiver,
        payment_factory: PaymentFactory,
    ) -> None:
        payment = await payment_factory.payment(webhook_url=webhook_receiver.url)
        start_worker("consumer")

        assert rabbit.close_connections() > 0
        wait_until(lambda: rabbit.publish("payments", "payments.new", {"payment_id": str(payment.id)}))

        wait_until(lambda: webhook_receiver.requests, message="consumer did not recover after connection loss")

    async def test_consumer_survives_lost_database_connections(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        webhook_receiver: WebhookReceiver,
        payment_factory: PaymentFactory,
        container: Container,
    ) -> None:
        first = await payment_factory.payment(webhook_url=webhook_receiver.url)
        second = await payment_factory.payment(webhook_url=webhook_receiver.url)
        start_worker("consumer")
        publish_payment_created(rabbit, first)
        wait_until(lambda: len(webhook_receiver.requests) == 1)

        # Сервер БД рвёт все чужие соединения, как при рестарте или переключении на реплику.
        async with container.db.db().engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()",
                ),
            )
        publish_payment_created(rabbit, second)

        wait_until(lambda: len(webhook_receiver.requests) == 2, message="consumer did not recover after DB loss")
        assert rabbit.peek("payments.new.dlq") == []


class TestRelayProcess:
    async def test_relay_publishes_outbox_and_stops_on_sigterm(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        uow: TestUow,
    ) -> None:
        relay = start_worker("relay")

        await create_payment(uow, "https://example.com/hook", key="relay-1")
        messages = wait_until(lambda: rabbit.peek("payments.new"), message="event is not published")

        async with uow:
            outbox = await uow.get_all_outbox()
        assert [record.status for record in outbox] == [OutboxStatusEnum.SUCCESS]
        assert messages[0]["body"] == outbox[0].payload
        wait_until(lambda: "outbox_published_total 1.0" in metrics_of(relay))
        assert relay.terminate() == 0

    async def test_relay_exposes_outbox_lag(
        self,
        start_worker: StartWorker,
        outbox_factory: OutboxFactory,
    ) -> None:
        # Запись, которую relay не сможет отправить: обменника с таким именем нет.
        await outbox_factory.outbox(exchange="no-such-exchange", created_at=utc_now() - timedelta(minutes=5))
        # Готовности не ждём: пока запись не уходит, relay честно не считает себя здоровым.
        relay = start_worker("relay", wait_ready=False)

        def has_lag() -> bool:
            try:
                metrics = metrics_of(relay)
            except OSError:
                return False
            lag = next(line for line in metrics.splitlines() if line.startswith("outbox_oldest_pending_age_seconds "))
            return "outbox_pending_records 1.0" in metrics and float(lag.split()[1]) >= 300

        wait_until(has_lag, message="outbox lag is not exposed")
        assert "outbox_publish_errors_total 0.0" not in metrics_of(relay)
        assert not relay.heartbeat_path.exists()

    async def test_relay_survives_lost_broker_connection(
        self,
        start_worker: StartWorker,
        rabbit: RabbitManagement,
        uow: TestUow,
    ) -> None:
        start_worker("relay")
        assert rabbit.close_connections() > 0

        await create_payment(uow, "https://example.com/hook", key="relay-2")

        wait_until(lambda: rabbit.peek("payments.new"), message="relay did not recover after connection loss")
        async with uow:
            assert [record.status for record in await uow.get_all_outbox()] == [OutboxStatusEnum.SUCCESS]


class TestCommands:
    async def test_clean_outbox_command(
        self, run_command: RunCommand, outbox_factory: OutboxFactory, uow: TestUow
    ) -> None:
        old = utc_now() - timedelta(days=10)
        await outbox_factory.outbox(status=OutboxStatusEnum.SUCCESS, processed_at=old)
        pending = await outbox_factory.outbox()

        result = run_command("clean-outbox")

        assert result.returncode == 0, result.stderr
        assert "Удалено записей outbox: 1" in result.stdout
        async with uow:
            assert [record.id for record in await uow.get_all_outbox()] == [pending.id]

    async def test_requeue_dlq_command(self, run_command: RunCommand, rabbit: RabbitManagement) -> None:
        # Первый запуск объявляет топологию в пустом vhost'е и ничего не переносит.
        assert "Возвращено сообщений в обработку: 0" in run_command("requeue-dlq").stdout
        body = {"payment_id": "11111111-1111-4111-8111-111111111111"}
        headers = {"x-retry-attempt": 2, "x-request-id": "req-proc"}
        assert rabbit.publish("payments.dlx", "payments.new", body, headers)
        wait_until(lambda: rabbit.peek("payments.new.dlq"))

        result = run_command("requeue-dlq")

        assert result.returncode == 0, result.stderr
        assert "Возвращено сообщений в обработку: 1" in result.stdout
        assert rabbit.peek("payments.new.dlq") == []
        messages = rabbit.peek("payments.new")
        assert [message["body"] for message in messages] == [body]
        # Счётчик попыток сброшен, идентификатор запроса сохранён.
        assert messages[0]["headers"] == {"x-request-id": "req-proc"}
