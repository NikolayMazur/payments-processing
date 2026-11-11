from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from prometheus_client import REGISTRY
from pytest_mock import MockerFixture

from app.app_layer.interfaces.publishers.dead_letters import AbstractDeadLetterQueue
from app.app_layer.interfaces.publishers.events import AbstractEventPublisher, EventPublishError
from app.app_layer.interfaces.services.outbox.exceptions import OutboxPublishError
from app.app_layer.services.outbox.clean_service import CleanOutboxService
from app.app_layer.services.outbox.collect_metrics_service import CollectQueueMetricsService
from app.app_layer.services.outbox.send_service import SendOutboxService
from app.config import OutboxSettings
from app.containers import Container
from app.domain.outbox.enums import OutboxStatusEnum
from app.utils import utc_now
from tests.environment.unit_of_work import TestUow
from tests.factories import OutboxFactory


class TestSendOutboxService:
    @pytest.fixture(autouse=True)
    def _setup_mocks(self, uow: TestUow, mocker: MockerFixture) -> None:
        self.uow = uow
        self.publisher: AsyncMock = mocker.AsyncMock(spec=AbstractEventPublisher)
        self.service = SendOutboxService(uow=uow, publisher=self.publisher, settings=OutboxSettings(BATCH_SIZE=2))

    async def test_pending_records_published_and_marked(self, outbox_factory: OutboxFactory) -> None:
        first = await outbox_factory.outbox()
        second = await outbox_factory.outbox()

        sent = await self.service.process()

        assert sent == 2
        assert [call.args[0].id for call in self.publisher.publish.await_args_list] == [first.id, second.id]
        async with self.uow:
            outbox = await self.uow.get_all_outbox()
        assert {record.status for record in outbox} == {OutboxStatusEnum.SUCCESS}
        assert all(record.processed_at is not None for record in outbox)

    async def test_batch_is_limited_and_sent_records_are_skipped(self, outbox_factory: OutboxFactory) -> None:
        records = [await outbox_factory.outbox() for _ in range(3)]

        assert await self.service.process() == 2
        assert await self.service.process() == 1
        assert await self.service.process() == 0

        published = [call.args[0].id for call in self.publisher.publish.await_args_list]
        assert published == [record.id for record in records]

    async def test_no_pending_records_nothing_published(self) -> None:
        assert await self.service.process() == 0

        self.publisher.publish.assert_not_awaited()

    async def test_publish_error_keeps_record_pending(self, outbox_factory: OutboxFactory) -> None:
        first = await outbox_factory.outbox()
        second = await outbox_factory.outbox()
        self.publisher.publish.side_effect = [None, EventPublishError]

        with pytest.raises(OutboxPublishError):
            await self.service.process()

        async with self.uow:
            statuses = {record.id: record.status for record in await self.uow.get_all_outbox()}
        # Первая запись уже принята брокером и второй раз не уйдёт, вторая дождётся следующего круга.
        assert statuses == {first.id: OutboxStatusEnum.SUCCESS, second.id: OutboxStatusEnum.PENDING}

        self.publisher.publish.side_effect = None
        assert await self.service.process() == 1
        assert self.publisher.publish.await_args.args[0].id == second.id

    async def test_records_locked_by_other_worker_are_skipped(
        self,
        outbox_factory: OutboxFactory,
        container: Container,
    ) -> None:
        locked = await outbox_factory.outbox()
        free = await outbox_factory.outbox()
        other_worker = TestUow(session_factory=container.db.db().session_factory)

        async with other_worker:
            taken = await other_worker.outbox.get_pending_batch_for_update(limit=1)
            sent = await self.service.process()

        assert [record.id for record in taken] == [locked.id]
        assert sent == 1
        assert self.publisher.publish.await_args.args[0].id == free.id


class TestCleanOutboxService:
    @pytest.fixture(autouse=True)
    def _setup(self, uow: TestUow) -> None:
        self.uow = uow
        self.service = CleanOutboxService(uow=uow, settings=OutboxSettings(RETENTION_DAYS=7, CLEAN_BATCH_SIZE=2))

    async def test_only_old_published_records_deleted(self, outbox_factory: OutboxFactory) -> None:
        now = utc_now()
        old = [
            await outbox_factory.outbox(status=OutboxStatusEnum.SUCCESS, processed_at=now - timedelta(days=8))
            for _ in range(5)
        ]
        fresh = await outbox_factory.outbox(status=OutboxStatusEnum.SUCCESS, processed_at=now - timedelta(days=6))
        # Неопубликованная запись остаётся при любом возрасте: её ещё предстоит отправить.
        pending = await outbox_factory.outbox(created_at=now - timedelta(days=30))

        deleted = await self.service.process()

        assert deleted == len(old)
        async with self.uow:
            left = {record.id for record in await self.uow.get_all_outbox()}
        assert left == {fresh.id, pending.id}

    async def test_retention_can_be_overridden(self, outbox_factory: OutboxFactory) -> None:
        await outbox_factory.outbox(status=OutboxStatusEnum.SUCCESS, processed_at=utc_now() - timedelta(hours=1))

        assert await self.service.process() == 0
        assert await self.service.process(retention_days=0) == 1


class TestCollectQueueMetricsService:
    async def test_gauges_show_outbox_lag_and_dlq_size(
        self,
        uow: TestUow,
        outbox_factory: OutboxFactory,
        mocker: MockerFixture,
    ) -> None:
        dead_letter_queue: AsyncMock = mocker.AsyncMock(spec=AbstractDeadLetterQueue)
        dead_letter_queue.count.return_value = 3
        service = CollectQueueMetricsService(uow=uow, dead_letter_queue=dead_letter_queue)
        await outbox_factory.outbox(created_at=utc_now() - timedelta(seconds=90))
        await outbox_factory.outbox()
        await outbox_factory.outbox(status=OutboxStatusEnum.SUCCESS, processed_at=utc_now())

        await service.process()

        assert REGISTRY.get_sample_value("outbox_pending_records") == 2
        assert 90 <= (REGISTRY.get_sample_value("outbox_oldest_pending_age_seconds") or 0) < 100
        assert REGISTRY.get_sample_value("dead_letter_queue_messages") == 3

    async def test_empty_outbox_has_zero_lag(self, uow: TestUow, mocker: MockerFixture) -> None:
        dead_letter_queue: AsyncMock = mocker.AsyncMock(spec=AbstractDeadLetterQueue)
        dead_letter_queue.count.return_value = 0

        await CollectQueueMetricsService(uow=uow, dead_letter_queue=dead_letter_queue).process()

        assert REGISTRY.get_sample_value("outbox_pending_records") == 0
        assert REGISTRY.get_sample_value("outbox_oldest_pending_age_seconds") == 0
