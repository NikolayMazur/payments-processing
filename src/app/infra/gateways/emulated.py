import asyncio
import random
from logging import getLogger

from app.app_layer.interfaces.clients.payment_gateway import AbstractPaymentGateway, GatewayChargeResultDTO
from app.config import GatewaySettings
from app.domain.payments.entity import Payment

logger = getLogger(__name__)

DECLINED_REASON = "Платёж отклонён шлюзом"


class EmulatedPaymentGateway(AbstractPaymentGateway):
    """Эмуляция внешнего платёжного шлюза: отвечает с задержкой и отклоняет часть платежей.

    Ведёт себя как идемпотентный шлюз: исход определяется идентификатором платежа, поэтому
    повторное обращение по тому же платежу возвращает тот же результат.
    """

    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings

    async def charge(self, payment: Payment) -> GatewayChargeResultDTO:
        # Случайность здесь изображает поведение шлюза и к безопасности отношения не имеет.
        rng = random.Random(payment.id.int)  # noqa: S311  # nosec B311
        delay = rng.uniform(self._settings.MIN_DELAY_SEC, self._settings.MAX_DELAY_SEC)
        logger.info("Charging payment %s, gateway will respond in %.1f sec", payment.id, delay)
        await asyncio.sleep(delay)
        if rng.random() < self._settings.SUCCESS_RATE:
            return GatewayChargeResultDTO(is_success=True)
        return GatewayChargeResultDTO(is_success=False, failure_reason=DECLINED_REASON)
