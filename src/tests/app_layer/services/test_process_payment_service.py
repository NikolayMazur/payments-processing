from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from app.app_layer.interfaces.clients.payment_gateway import AbstractPaymentGateway, GatewayChargeResultDTO
from app.app_layer.interfaces.clients.webhook import AbstractWebhookClient, PaymentWebhookDTO, WebhookDeliveryError
from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.app_layer.services.payments.process_payment_service import ProcessPaymentService
from app.domain.payments.enums import PaymentStatusEnum
from app.utils import utc_now
from tests.environment.unit_of_work import TestUow
from tests.factories import PaymentFactory


class TestProcessPaymentService:
    @pytest.fixture(autouse=True)
    def _setup_mocks(self, uow: TestUow, mocker: MockerFixture) -> None:
        self.uow = uow
        self.payment_gateway: AsyncMock = mocker.AsyncMock(spec=AbstractPaymentGateway)
        self.payment_gateway.charge.return_value = GatewayChargeResultDTO(is_success=True)
        self.webhook_client: AsyncMock = mocker.AsyncMock(spec=AbstractWebhookClient)
        self.service = ProcessPaymentService(
            uow=uow,
            payment_gateway=self.payment_gateway,
            webhook_client=self.webhook_client,
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

    async def test_concurrent_handler_result_is_not_overwritten(self, payment_factory: PaymentFactory) -> None:
        payment = await payment_factory.payment()

        async def charge_while_other_handler_finishes(*_: object) -> GatewayChargeResultDTO:
            # Пока шлюз отвечал нам, параллельный обработчик того же события уже записал отказ.
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

    async def test_unknown_payment_not_found(self) -> None:
        with pytest.raises(PaymentNotFoundError):
            await self.service.process(ProcessPaymentInputDTO(payment_id=uuid4()))

        self.payment_gateway.charge.assert_not_awaited()
        self.webhook_client.send.assert_not_awaited()
