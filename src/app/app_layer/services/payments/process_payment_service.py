from logging import getLogger

from app.app_layer import metrics
from app.app_layer.interfaces.clients.payment_gateway import AbstractPaymentGateway
from app.app_layer.interfaces.clients.webhook import AbstractWebhookClient, PaymentWebhookDTO, WebhookDeliveryError
from app.app_layer.interfaces.locks.payment import AbstractPaymentLock
from app.app_layer.interfaces.services.payments.dto import ProcessPaymentInputDTO
from app.app_layer.interfaces.services.payments.exceptions import PaymentNotFoundError
from app.app_layer.interfaces.services.payments.services import AbstractProcessPaymentService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.domain.payments.entity import Payment
from app.domain.payments.enums import PaymentStatusEnum
from app.utils import utc_now

logger = getLogger(__name__)


class ProcessPaymentService(AbstractProcessPaymentService):
    """Проводит платёж через шлюз, сохраняет итог и уведомляет клиента вебхуком.

    Сервис идемпотентен: при повторной доставке события платёж второй раз не проводится,
    а уже принятый получателем вебхук не отправляется заново.

    Платёж на время обработки захватывается блокировкой. Копия того же события, пришедшая одновременно
    (брокер доставил дубль, relay опубликовал запись дважды), получает PaymentLockedError и в работу не идёт:
    иначе оба обработчика сходили бы в шлюз и оба отправили бы вебхук.
    """

    def __init__(
        self,
        uow: AbcUnitOfWork,
        payment_gateway: AbstractPaymentGateway,
        webhook_client: AbstractWebhookClient,
        payment_lock: AbstractPaymentLock,
    ) -> None:
        self._payment_lock = payment_lock
        self._uow = uow
        self._payment_gateway = payment_gateway
        self._webhook_client = webhook_client

    async def process(self, data: ProcessPaymentInputDTO) -> None:
        async with self._payment_lock.acquire(data.payment_id):
            await self._process(data)

    async def _process(self, data: ProcessPaymentInputDTO) -> None:
        async with self._uow:
            payment = await self._uow.payment.get_by_id(data.payment_id)
        if payment is None:
            raise PaymentNotFoundError

        if payment.is_pending:
            payment = await self._charge(payment)
        else:
            logger.info("Payment %s is already processed, charge is skipped", payment.id)

        if payment.is_webhook_delivered:
            logger.info("Webhook for payment %s is already delivered, nothing to send", payment.id)
            return
        await self._send_webhook(payment)

    async def _charge(self, payment: Payment) -> Payment:
        # Шлюз отвечает секундами, поэтому зовём его вне транзакции и соединение с БД не держим.
        result = await self._payment_gateway.charge(payment)
        status = PaymentStatusEnum.SUCCEEDED if result.is_success else PaymentStatusEnum.FAILED
        async with self._uow:
            updated = await self._uow.payment.set_result_if_pending(
                payment_id=payment.id,
                status=status,
                failure_reason=result.failure_reason,
                processed_at=utc_now(),
            )
            is_saved_by_us = updated is not None
            if updated is None:
                # Итог уже есть в БД: его успела записать прошлая попытка, чей ответ мы не дождались.
                updated = await self._uow.payment.get_by_id(payment.id)
        if updated is None:
            raise PaymentNotFoundError
        if is_saved_by_us and updated.processed_at is not None:
            metrics.payments_processed.labels(status=updated.status.value).inc()
            metrics.payment_processing_duration.observe((updated.processed_at - updated.created_at).total_seconds())
        logger.info("Payment %s processed with status %s", updated.id, updated.status.value)
        return updated

    async def _send_webhook(self, payment: Payment) -> None:
        try:
            await self._webhook_client.send(url=payment.webhook_url, data=self._build_webhook(payment))
        except WebhookDeliveryError:
            metrics.webhook_deliveries.labels(result="failed").inc()
            await self._register_webhook_attempt(payment, is_delivered=False)
            raise
        metrics.webhook_deliveries.labels(result="delivered").inc()
        await self._register_webhook_attempt(payment, is_delivered=True)
        logger.info("Webhook for payment %s delivered", payment.id)

    async def _register_webhook_attempt(self, payment: Payment, is_delivered: bool) -> None:
        async with self._uow:
            await self._uow.payment.register_webhook_attempt(
                payment_id=payment.id,
                delivered_at=utc_now() if is_delivered else None,
            )

    @staticmethod
    def _build_webhook(payment: Payment) -> PaymentWebhookDTO:
        return PaymentWebhookDTO(payment_id=payment.id, **payment.model_dump(exclude={"id"}))
