from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tests.factories import PaymentFactory


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1.00")])
async def test_database_rejects_not_positive_amount(payment_factory: PaymentFactory, amount: Decimal) -> None:
    # API такую сумму не пропустит, но ограничение в БД страхует от записи в обход него.
    with pytest.raises(IntegrityError, match="ck_payments_amount_positive"):
        await payment_factory.payment(amount=amount)
