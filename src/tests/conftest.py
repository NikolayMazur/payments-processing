import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.events.rabbit.consumers import payment_created
from app.api.rest.main import app, lifespan
from app.config import DatabaseSettings, config
from app.containers import Container
from app.infra.db.database import Database
from app.infra.repositories.sqla.base import metadata
from tests.constants import TEST_API_KEY
from tests.environment.unit_of_work import TestUow
from tests.factories import OutboxFactory, PaymentFactory

SRC_DIR = Path(__file__).resolve().parent.parent


def run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DB__URL"] = db_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        env=env,
        cwd=SRC_DIR,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def db_settings() -> DatabaseSettings:
    return config.DB.model_copy(update={"URL": f"{config.DB.URL}_test"})


async def recreate_database(db_url: str) -> None:
    url = make_url(db_url)
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def database(db_settings: DatabaseSettings) -> None:
    """Пересоздаёт тестовую базу и накатывает схему настоящими миграциями."""
    await recreate_database(db_settings.URL)
    run_alembic(db_settings.URL, "upgrade", "head")


@pytest.fixture(scope="session")
async def container(db_settings: DatabaseSettings) -> AsyncIterator[Container]:
    async with lifespan(app):
        app_container: Container = app.state.container
        app_container.db.db.override(providers.Singleton(Database, settings=db_settings))
        app_container.wire(modules=[payment_created])
        yield app_container


@pytest.fixture(autouse=True)
async def _clean_db(container: Container) -> AsyncIterator[None]:
    yield
    async with container.db.db().engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture(autouse=True)
def _api_key(mocker: MockerFixture) -> None:
    mocker.patch.object(config.AUTH, "API_KEY", TEST_API_KEY)


@pytest.fixture()
def uow(container: Container) -> TestUow:
    return TestUow(session_factory=container.db.db().session_factory)


@pytest.fixture()
def payment_factory(uow: TestUow) -> PaymentFactory:
    return PaymentFactory(uow=uow)


@pytest.fixture()
def outbox_factory(uow: TestUow) -> OutboxFactory:
    return OutboxFactory(uow=uow)


@pytest.fixture()
async def client(container: Container) -> AsyncIterator[AsyncClient]:
    headers = {config.AUTH.HEADER_NAME: TEST_API_KEY}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        yield client
