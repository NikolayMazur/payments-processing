from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_layer.interfaces.repositories.health import AbcHealthRepository


class HealthRepository(AbcHealthRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ping_database(self) -> None:
        await self._session.execute(text("SELECT 1"))
