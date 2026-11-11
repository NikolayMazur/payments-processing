from decimal import Decimal

import pytest
from pytest_mock import MockerFixture

from app.app_layer.interfaces.services.payments.dto import CreatePaymentInputDTO
from app.app_layer.interfaces.services.payments.exceptions import IdempotencyKeyConflictError
from app.app_layer.services.payments.create_payment_service import CreatePaymentService
from app.config import config
from app.domain.payments.enums import CurrencyEnum
from app.infra.repositories.sqla.outbox import OutboxRepository
from tests.constants import TEST_WEBHOOK_URL
from tests.environment.unit_of_work import TestUow


@pytest.fixture()
def service(uow: TestUow) -> CreatePaymentService:
    return CreatePaymentService(uow=uow, rabbit_settings=config.RABBIT)


def build_input(**kwargs: object) -> CreatePaymentInputDTO:
    data = {
        "idempotency_key": "key-1",
        "amount": Decimal("10.5"),
        "currency": CurrencyEnum.EUR,
        "description": "Подписка",
        "metadata": {"b": 2, "a": 1},
        "webhook_url": TEST_WEBHOOK_URL,
    }
    return CreatePaymentInputDTO.model_validate(data | kwargs)


class TestCreatePaymentService:
    async def test_payment_and_event_are_saved_together(self, service: CreatePaymentService, uow: TestUow) -> None:
        result = await service.process(build_input())

        async with uow:
            payment = await uow.payment.get_by_id(result.payment_id)
            outbox = await uow.get_all_outbox()
        assert payment is not None
        assert payment.amount == Decimal("10.50")
        assert [record.event_key for record in outbox] == [str(result.payment_id)]

    async def test_outbox_failure_rolls_back_payment(
        self,
        service: CreatePaymentService,
        uow: TestUow,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch.object(OutboxRepository, "create_from_entity", side_effect=RuntimeError("outbox is down"))

        with pytest.raises(RuntimeError):
            await service.process(build_input())

        async with uow:
            assert await uow.count_payments() == 0
            assert await uow.get_all_outbox() == []

    async def test_metadata_key_order_does_not_change_request(self, service: CreatePaymentService) -> None:
        first = await service.process(build_input(metadata={"a": 1, "b": 2}))

        second = await service.process(build_input(metadata={"b": 2, "a": 1}))

        assert second == first

    @pytest.mark.parametrize(
        "changed",
        [
            {"amount": Decimal("10.51")},
            {"currency": CurrencyEnum.USD},
            {"description": "Другая подписка"},
            {"metadata": {"a": 1}},
            {"webhook_url": "https://other.example.com/hook"},
        ],
    )
    async def test_changed_field_with_same_key_conflict(
        self,
        service: CreatePaymentService,
        changed: dict[str, object],
    ) -> None:
        await service.process(build_input())

        with pytest.raises(IdempotencyKeyConflictError):
            await service.process(build_input(**changed))
