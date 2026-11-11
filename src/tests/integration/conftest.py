from collections.abc import AsyncIterator

import pytest
from faststream.rabbit import RabbitBroker

from app.config import config
from app.containers import Container
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import declare_topology
from app.infra.rabbit.workers import payments_new  # noqa: F401  регистрирует подписчика на брокере
from tests.rabbit_utils import RabbitManagement


@pytest.fixture(scope="package")
def rabbit_vhost() -> None:
    """Пересоздаёт отдельный vhost: в нём нет чужих консьюмеров и сообщений от прошлых прогонов."""
    RabbitManagement(config.RABBIT.URL).recreate_vhost()


@pytest.fixture(scope="package")
async def rabbit_broker(rabbit_vhost: None, container: Container) -> AsyncIterator[RabbitBroker]:
    await broker.connect()
    await declare_topology(broker)
    await broker.start()
    yield broker
    await broker.stop()
