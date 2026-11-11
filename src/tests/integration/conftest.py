import os
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
import pytest
from faststream.rabbit import RabbitBroker
from sqlalchemy.engine import make_url

from app.config import config
from app.containers import Container
from app.infra.rabbit.broker import broker
from app.infra.rabbit.topology import declare_topology
from app.infra.rabbit.workers import payments_new  # noqa: F401  регистрирует подписчика на брокере

MANAGEMENT_URL = os.environ.get("RABBIT_MANAGEMENT_URL", "http://localhost:15672")


@pytest.fixture(scope="package")
async def rabbit_vhost() -> str:
    """Пересоздаёт отдельный vhost: в нём нет чужих консьюмеров и сообщений от прошлых прогонов."""
    url = make_url(config.RABBIT.URL.replace("amqp://", "http://"))
    vhost = quote(url.database or "/", safe="")
    auth = (url.username or "guest", url.password or "guest")
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, auth=auth, timeout=10) as client:
        await client.delete(f"/api/vhosts/{vhost}")
        (await client.put(f"/api/vhosts/{vhost}")).raise_for_status()
        permissions = {"configure": ".*", "write": ".*", "read": ".*"}
        (await client.put(f"/api/permissions/{vhost}/{auth[0]}", json=permissions)).raise_for_status()
    return vhost


@pytest.fixture(scope="package")
async def rabbit_broker(rabbit_vhost: str, container: Container) -> AsyncIterator[RabbitBroker]:
    await broker.connect()
    await declare_topology(broker)
    await broker.start()
    yield broker
    await broker.stop()
