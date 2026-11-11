from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from app.config import GatewaySettings
from app.domain.payments.entity import Payment
from app.infra.gateways.emulated import DECLINED_REASON, EmulatedPaymentGateway
from tests.factories import PaymentFactory


def build_payment() -> Payment:
    return Payment(**PaymentFactory.base_generate(), id=uuid4())


@pytest.fixture()
def payment() -> Payment:
    return build_payment()


async def test_gateway_waits_configured_delay(payment: Payment, mocker: MockerFixture) -> None:
    sleep = mocker.patch("app.infra.gateways.emulated.asyncio.sleep")
    gateway = EmulatedPaymentGateway(GatewaySettings(MIN_DELAY_SEC=2, MAX_DELAY_SEC=5, SUCCESS_RATE=1))

    result = await gateway.charge(payment)

    assert result.is_success is True
    assert 2 <= sleep.await_args.args[0] <= 5


async def test_gateway_declines_with_reason(payment: Payment) -> None:
    gateway = EmulatedPaymentGateway(GatewaySettings(MIN_DELAY_SEC=0, MAX_DELAY_SEC=0, SUCCESS_RATE=0))

    result = await gateway.charge(payment)

    assert result.is_success is False
    assert result.failure_reason == DECLINED_REASON


async def test_gateway_success_rate_is_close_to_configured() -> None:
    gateway = EmulatedPaymentGateway(GatewaySettings(MIN_DELAY_SEC=0, MAX_DELAY_SEC=0, SUCCESS_RATE=0.9))

    results = [await gateway.charge(build_payment()) for _ in range(2000)]

    success_share = sum(result.is_success for result in results) / len(results)
    assert 0.85 < success_share < 0.95


async def test_repeated_charge_of_same_payment_returns_same_result() -> None:
    """Шлюз идемпотентен по идентификатору платежа: повторное обращение не может дать другой исход."""
    gateway = EmulatedPaymentGateway(GatewaySettings(MIN_DELAY_SEC=0, MAX_DELAY_SEC=0, SUCCESS_RATE=0.5))
    payments = [build_payment() for _ in range(200)]

    first = [await gateway.charge(payment) for payment in payments]
    second = [await gateway.charge(payment) for payment in payments]

    assert first == second
    # Проверка не вырождена: среди платежей есть и успешные, и отклонённые.
    assert {result.is_success for result in first} == {True, False}
