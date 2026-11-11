from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import DatabaseSettings


class Database:
    def __init__(self, settings: DatabaseSettings) -> None:
        self._engine = create_async_engine(
            settings.URL,
            pool_size=settings.POOL_SIZE,
            max_overflow=settings.MAX_OVERFLOW,
            pool_pre_ping=True,
            echo=settings.ECHO,
        )
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def shutdown(self) -> None:
        await self._engine.dispose()
