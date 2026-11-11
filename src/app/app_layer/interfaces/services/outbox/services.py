from abc import ABC, abstractmethod


class AbstractSendOutboxService(ABC):
    @abstractmethod
    async def process(self) -> int:
        """Публикует пачку записей outbox. Возвращает число опубликованных."""
        raise NotImplementedError
