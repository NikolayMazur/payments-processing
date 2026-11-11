import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from app.app_layer.interfaces.clients.payment_gateway import AbstractPaymentGateway, GatewayChargeResultDTO
from app.app_layer.interfaces.clients.webhook import AbstractWebhookClient, PaymentWebhookDTO, WebhookDeliveryError
from app.app_layer.interfaces.locks.payment import PaymentLockedError
from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.app_layer.interfaces.unit_of_work.exceptions import DatabaseUnavailableError
from app.app_layer.services.payments.process_payment_service import ProcessPaymentService
from app.containers import Container
from app.domain.payments.enums import PaymentStatusEnum
from app.infra.repositories.sqla.payment import PaymentRepository
from app.utils import utc_now
from tests.environment.unit_of_work import TestUow
from tests.factories import PaymentFactory


class TestProcessPaymentService:
    @pytest.fixture(autouse=True)
    def _setup_mocks(self, uow: TestUow, container: Container, mocker: MockerFixture) -> None:
        self.uow = uow
        self.payment_gateway: AsyncMock = mocker.AsyncMock(spec=AbstractPaymentGateway)
        self.payment_gateway.charge.return_value = GatewayChargeResultDTO(is_success=True)
        self.webhook_client: AsyncMock = mocker.AsyncMock(spec=AbstractWebhookClient)
        self.service = ProcessPaymentService(
            uow=uow,
            payment_gateway=self.payment_gateway,
            webhook_client=self.webhook_client,
            payment_lock=container.db.payment_lock(),
        )

    async def test_successful_charge_saved_and_webhook_sent(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()

        await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.status is PaymentStatusEnum.SUCCEEDED
        assert saved.processed_at is not None
        assert saved.failure_reason is None
        self.payment_gateway.charge.assert_awaited_once()
        self.webhook_client.send.assert_awaited_once()
        call = self.webhook_client.send.await_args
        assert call.kwargs["url"] == payment.webhook_url
        webhook: PaymentWebhookDTO = call.kwargs["data"]
        assert webhook.payment_id == payment.id
        assert webhook.status is PaymentStatusEnum.SUCCEEDED
        assert webhook.amount == payment.amount
        assert webhook.metadata == payment.metadata
        assert webhook.processed_at == saved.processed_at
        assert saved.webhook_attempts == 1
        assert saved.webhook_delivered_at is not None

    async def test_declined_charge_saved_as_failed(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()
        self.payment_gateway.charge.return_value = GatewayChargeResultDTO(is_success=False, failure_reason="declined")

        await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.status is PaymentStatusEnum.FAILED
        assert saved.failure_reason == "declined"
        webhook: PaymentWebhookDTO = self.webhook_client.send.await_args.kwargs["data"]
        assert webhook.status is PaymentStatusEnum.FAILED
        assert webhook.failure_reason == "declined"

    async def test_webhook_error_keeps_result_and_propagates(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()
        self.webhook_client.send.side_effect = WebhookDeliveryError

        with pytest.raises(WebhookDeliveryError):
            await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.status is PaymentStatusEnum.SUCCEEDED
        assert saved.webhook_attempts == 1
        assert saved.webhook_delivered_at is None

    async def test_retry_does_not_charge_again(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()
        self.webhook_client.send.side_effect = [WebhookDeliveryError, None]
        with pytest.raises(WebhookDeliveryError):
            await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        self.payment_gateway.charge.assert_awaited_once()
        assert self.webhook_client.send.await_count == 2
        first, second = (call.kwargs["data"] for call in self.webhook_client.send.await_args_list)
        assert first == second
        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.webhook_attempts == 2
        assert saved.webhook_delivered_at is not None

    async def test_duplicate_message_does_not_resend_delivered_webhook(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()
        await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        # Брокер доставил то же сообщение второй раз, например после потери подтверждения.
        await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        self.payment_gateway.charge.assert_awaited_once()
        self.webhook_client.send.assert_awaited_once()
        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.webhook_attempts == 1

    async def test_result_saved_earlier_is_not_overwritten(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()

        async def charge_while_other_handler_finishes(*_: object) -> GatewayChargeResultDTO:
            # Пока шлюз отвечал нам, в БД уже появился итог: его записала прошлая попытка,
            # у которой запись прошла, а подтверждение о ней потерялось.
            async with self.uow:
                await self.uow.payment.set_result_if_pending(
                    payment_id=payment.id,
                    status=PaymentStatusEnum.FAILED,
                    failure_reason="declined",
                    processed_at=utc_now(),
                )
            return GatewayChargeResultDTO(is_success=True)

        self.payment_gateway.charge.side_effect = charge_while_other_handler_finishes

        await self.service.process(ProcessPaymentInputDTO(payment_id=payment.id))

        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.status is PaymentStatusEnum.FAILED
        webhook: PaymentWebhookDTO = self.webhook_client.send.await_args.kwargs["data"]
        assert webhook.status is PaymentStatusEnum.FAILED

    async def test_copy_of_message_is_not_processed_while_payment_is_in_progress(
        self,
        payment_factory: PaymentFactory,
        container: Container,
        mocker: MockerFixture,
    ) -> None:
        """Одно и то же событие одновременно получают два консьюмера, каждый со своим соединением.

        Второй видит, что платёж занят, и в работу не идёт: в шлюз уходит одно обращение, клиенту один вебхук.
        """
        payment = await payment_factory.payment()
        in_gateway = asyncio.Event()
        release_gateway = asyncio.Event()

        async def charge(*_: object) -> GatewayChargeResultDTO:
            in_gateway.set()
            await release_gateway.wait()
            return GatewayChargeResultDTO(is_success=True)

        self.payment_gateway.charge.side_effect = charge
        other_webhook_client: AsyncMock = mocker.AsyncMock(spec=AbstractWebhookClient)
        other_consumer = ProcessPaymentService(
            uow=TestUow(session_factory=container.db.db().session_factory),
            payment_gateway=self.payment_gateway,
            webhook_client=other_webhook_client,
            payment_lock=container.db.payment_lock(),
        )
        data = ProcessPaymentInputDTO(payment_id=payment.id)

        first = asyncio.create_task(self.service.process(data))
        await in_gateway.wait()
        with pytest.raises(PaymentLockedError):
            await other_consumer.process(data)
        release_gateway.set()
        await first

        self.payment_gateway.charge.assert_awaited_once()
        self.webhook_client.send.assert_awaited_once()
        other_webhook_client.send.assert_not_awaited()
        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.status is PaymentStatusEnum.SUCCEEDED
        assert saved.webhook_attempts == 1

    async def test_payment_is_released_after_processing_and_after_failure(
        self,
        payment_factory: PaymentFactory,
    ) -> None:
        payment = await payment_factory.payment()
        data = ProcessPaymentInputDTO(payment_id=payment.id)
        self.webhook_client.send.side_effect = [WebhookDeliveryError, None]

        with pytest.raises(WebhookDeliveryError):
            await self.service.process(data)
        # Блокировка снята и после ошибки: повтор того же сообщения проходит.
        await self.service.process(data)

        assert self.webhook_client.send.await_count == 2

    async def test_different_payments_are_processed_in_parallel(
        self,
        payment_factory: PaymentFactory,
        container: Container,
    ) -> None:
        payments = [await payment_factory.payment() for _ in range(5)]
        all_in_gateway = asyncio.Event()
        in_gateway = 0

        async def charge(*_: object) -> GatewayChargeResultDTO:
            nonlocal in_gateway
            in_gateway += 1
            if in_gateway == len(payments):
                all_in_gateway.set()
            # Если бы блокировка была общей, сюда одновременно дошёл бы только один платёж.
            await asyncio.wait_for(all_in_gateway.wait(), timeout=5)
            return GatewayChargeResultDTO(is_success=True)

        self.payment_gateway.charge.side_effect = charge
        # Как в консьюмере: на каждое сообщение свой экземпляр сервиса со своим UoW.
        services = [
            ProcessPaymentService(
                uow=TestUow(session_factory=container.db.db().session_factory),
                payment_gateway=self.payment_gateway,
                webhook_client=self.webhook_client,
                payment_lock=container.db.payment_lock(),
            )
            for _ in payments
        ]

        await asyncio.gather(
            *(
                service.process(ProcessPaymentInputDTO(payment_id=payment.id))
                for service, payment in zip(services, payments, strict=True)
            ),
        )

        assert self.webhook_client.send.await_count == len(payments)

    async def test_webhook_is_sent_again_if_delivery_was_not_recorded(
        self,
        payment_factory: PaymentFactory,
        mocker: MockerFixture,
    ) -> None:
        """Ожидаемое поведение at-least-once, а не ошибка.

        Получатель принял вебхук, но сервис упал до записи об этом в БД. Сообщение вернётся на повтор,
        и вебхук уйдёт второй раз с тем же webhook-id. Поэтому получатель обязан отсекать дубли.
        """
        payment = await payment_factory.payment()
        original = PaymentRepository.register_webhook_attempt
        failures = [ConnectionRefusedError("database is down")]

        async def lose_first_record(repository: PaymentRepository, *args: Any, **kwargs: Any) -> None:
            if failures:
                raise failures.pop()
            await original(repository, *args, **kwargs)

        mocker.patch.object(PaymentRepository, "register_webhook_attempt", lose_first_record)
        data = ProcessPaymentInputDTO(payment_id=payment.id)

        with pytest.raises(DatabaseUnavailableError):
            await self.service.process(data)
        await self.service.process(data)

        self.payment_gateway.charge.assert_awaited_once()
        assert self.webhook_client.send.await_count == 2
        first, second = (call.kwargs["data"] for call in self.webhook_client.send.await_args_list)
        assert first == second
        async with self.uow:
            saved = await self.uow.payment.get_by_id(payment.id)
        assert saved is not None
        assert saved.webhook_attempts == 1
        assert saved.webhook_delivered_at is not None

    async def test_unknown_payment_not_found(self) -> None:
        with pytest.raises(PaymentNotFoundError):
            await self.service.process(ProcessPaymentInputDTO(payment_id=uuid4()))

        self.payment_gateway.charge.assert_not_awaited()
        self.webhook_client.send.assert_not_awaited()
