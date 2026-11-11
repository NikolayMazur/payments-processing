from abc import ABC, abstractmethod

from app.domain.outbox.entity import Outbox


class EventPublishError(Exception):
    """Брокер не принял сообщение"""


class AbstractEventPublisher(ABC):
    @abstractmethod
    async def publish(self, record: Outbox) -> None:
        raise NotImplementedError

    @abstractmethod
    async def ping(self) -> bool:
        """Проверяет, что брокер сейчас доступен."""
        raise NotImplementedError
