from logging import getLogger

from app.app_layer.interfaces.services.health.exceptions import DatabaseConnectionError
from app.app_layer.interfaces.services.health.services import AbstractHealthService
from app.app_layer.interfaces.unit_of_work.sql import AbcUnitOfWork

logger = getLogger(__name__)


class HealthService(AbstractHealthService):
    def __init__(self, uow: AbcUnitOfWork) -> None:
        self._uow = uow

    async def process(self) -> None:
        try:
            async with self._uow:
                await self._uow.health.ping_database()
        except Exception as exc:
            logger.exception("Database is unavailable")
            raise DatabaseConnectionError from exc
