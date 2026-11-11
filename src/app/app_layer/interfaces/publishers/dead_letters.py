from abc import ABC, abstractmethod


class AbstractDeadLetterQueue(ABC):
    @abstractmethod
    async def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def requeue(self, limit: int | None = None) -> int:
        """Возвращает сообщения из DLQ в основную очередь. Возвращает число перенесённых."""
        raise NotImplementedError
