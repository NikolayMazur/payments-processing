from abc import ABC, abstractmethod


class AbstractSendOutboxService(ABC):
    @abstractmethod
    async def process(self) -> int:
        """Публикует пачку записей outbox. Возвращает число опубликованных."""
        raise NotImplementedError


class AbstractCleanOutboxService(ABC):
    @abstractmethod
    async def process(self, retention_days: int | None = None) -> int:
        raise NotImplementedError


class AbstractCollectQueueMetricsService(ABC):
    @abstractmethod
    async def process(self) -> None:
        raise NotImplementedError
