import asyncio
import random
from logging import getLogger

from app.app_layer.interfaces.clients.payment_gateway import AbstractPaymentGateway, GatewayChargeResultDTO
from app.config import GatewaySettings
from app.domain.payments.entity import Payment

logger = getLogger(__name__)

DECLINED_REASON = "Платёж отклонён шлюзом"


class EmulatedPaymentGateway(AbstractPaymentGateway):
    """Эмуляция внешнего платёжного шлюза: отвечает с задержкой и отклоняет часть платежей."""

    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings

    async def charge(self, payment: Payment) -> GatewayChargeResultDTO:
        delay = random.uniform(self._settings.MIN_DELAY_SEC, self._settings.MAX_DELAY_SEC)  # noqa: S311
        logger.info("Charging payment %s, gateway will respond in %.1f sec", payment.id, delay)
        await asyncio.sleep(delay)
        if random.random() < self._settings.SUCCESS_RATE:  # noqa: S311
            return GatewayChargeResultDTO(is_success=True)
        return GatewayChargeResultDTO(is_success=False, failure_reason=DECLINED_REASON)
