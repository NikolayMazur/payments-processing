from logging import getLogger

from app.app_layer.interfaces.clients.payment_gateway import AbstractPaymentGateway
from app.app_layer.interfaces.clients.webhook import AbstractWebhookClient, PaymentWebhookDTO
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
    повторяется только отправка вебхука.
    """

    def __init__(
        self,
        uow: AbcUnitOfWork,
        payment_gateway: AbstractPaymentGateway,
        webhook_client: AbstractWebhookClient,
    ) -> None:
        self._uow = uow
        self._payment_gateway = payment_gateway
        self._webhook_client = webhook_client

    async def process(self, data: ProcessPaymentInputDTO) -> None:
        async with self._uow:
            payment = await self._uow.payment.get_by_id(data.payment_id)
        if payment is None:
            raise PaymentNotFoundError

        if payment.is_pending:
            payment = await self._charge(payment)
        else:
            logger.info("Payment %s is already processed, only webhook will be sent", payment.id)

        await self._webhook_client.send(url=payment.webhook_url, data=self._build_webhook(payment))
        logger.info("Webhook for payment %s delivered", payment.id)

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
            if updated is None:
                # Итог уже записал параллельный обработчик того же события, берём его результат.
                updated = await self._uow.payment.get_by_id(payment.id)
        if updated is None:
            raise PaymentNotFoundError
        logger.info("Payment %s processed with status %s", updated.id, updated.status.value)
        return updated

    @staticmethod
    def _build_webhook(payment: Payment) -> PaymentWebhookDTO:
        return PaymentWebhookDTO(payment_id=payment.id, **payment.model_dump(exclude={"id"}))
