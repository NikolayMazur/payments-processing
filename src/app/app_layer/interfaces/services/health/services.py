from abc import ABC, abstractmethod


class AbstractHealthService(ABC):
    @abstractmethod
    async def process(self) -> None:
        raise NotImplementedError
