from abc import ABC, abstractmethod


class AbcHealthRepository(ABC):
    @abstractmethod
    async def ping_database(self) -> None:
        raise NotImplementedError
