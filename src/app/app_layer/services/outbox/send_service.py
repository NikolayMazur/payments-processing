from logging import getLogger
from uuid import UUID

from app.app_layer.interfaces.publishers.events import AbstractEventPublisher, EventPublishError
from app.app_layer.interfaces.services.outbox.exceptions import OutboxPublishError
from app.app_layer.interfaces.services.outbox.services import AbstractSendOutboxService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork
from app.config import OutboxSettings
from app.utils import utc_now

logger = getLogger(__name__)


class SendOutboxService(AbstractSendOutboxService):
    """Публикует события из outbox в брокер.

    Доставка at-least-once: если процесс упадёт между публикацией и коммитом, запись уйдёт повторно.
    Дубли гасит потребитель.
    """

    def __init__(self, uow: AbcUnitOfWork, publisher: AbstractEventPublisher, settings: OutboxSettings) -> None:
        self._uow = uow
        self._publisher = publisher
        self._settings = settings

    async def process(self) -> int:
        sent_ids: list[UUID] = []
        error: EventPublishError | None = None
        async with self._uow:
            batch = await self._uow.outbox.get_pending_batch_for_update(limit=self._settings.BATCH_SIZE)
            for record in batch:
                try:
                    await self._publisher.publish(record)
                except EventPublishError as exc:
                    logger.exception("Could not publish outbox record %s", record.id)
                    error = exc
                    break
                sent_ids.append(record.id)
            if sent_ids:
                # Уже принятые брокером записи помечаем даже при сбое на середине пачки,
                # иначе после отката они уйдут второй раз.
                await self._uow.outbox.mark_batch_success(ids=sent_ids, processed_at=utc_now())

        if error is not None:
            raise OutboxPublishError from error
        return len(sent_ids)
