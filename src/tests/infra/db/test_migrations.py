from collections.abc import AsyncIterator

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import Script, ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import DatabaseSettings
from tests.conftest import SRC_DIR, recreate_database, run_alembic


def get_revisions() -> list[Script]:
    alembic_config = AlembicConfig(str(SRC_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(SRC_DIR / "alembic"))
    revisions = list(ScriptDirectory.from_config(alembic_config).walk_revisions("base", "heads"))
    return revisions[::-1]


REVISIONS = get_revisions()
FIRST_REVISION = REVISIONS[0].revision


@pytest.fixture(scope="module")
async def migrations_db_url(db_settings: DatabaseSettings) -> str:
    """Отдельная пустая база: миграции гоняются вверх и вниз и не мешают остальным тестам."""
    url = f"{db_settings.URL}_migrations"
    await recreate_database(url)
    return url


@pytest.fixture()
async def connection(migrations_db_url: str) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(migrations_db_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


def test_models_match_migrations(db_settings: DatabaseSettings) -> None:
    result = run_alembic(db_settings.URL, "check")

    assert "No new upgrade operations detected" in result.stdout + result.stderr


def test_single_head(db_settings: DatabaseSettings) -> None:
    result = run_alembic(db_settings.URL, "heads")

    assert len(result.stdout.strip().splitlines()) == 1


@pytest.mark.parametrize("revision", REVISIONS, ids=lambda revision: revision.revision)
def test_each_revision_upgrades_and_downgrades(migrations_db_url: str, revision: Script) -> None:
    """Лесенка: каждая ревизия накатывается, откатывается и накатывается снова.

    Ловит миграцию, у которой downgrade не возвращает схему в исходное состояние.
    """
    run_alembic(migrations_db_url, "upgrade", revision.revision)
    run_alembic(migrations_db_url, "downgrade", str(revision.down_revision or "base"))
    run_alembic(migrations_db_url, "upgrade", revision.revision)


async def test_existing_rows_survive_upgrade_and_downgrade(migrations_db_url: str, connection: AsyncConnection) -> None:
    """Миграции накатываются на базу, в которой уже есть платежи и события первой версии схемы."""
    run_alembic(migrations_db_url, "downgrade", "base")
    run_alembic(migrations_db_url, "upgrade", FIRST_REVISION)
    await connection.execute(
        text(
            "INSERT INTO payments (id, amount, currency, status, idempotency_key, request_hash, webhook_url) "
            "VALUES ('11111111-1111-4111-8111-111111111111', 100.50, 'RUB', 'succeeded', 'old-key', 'hash', "
            "'https://example.com/hook')",
        ),
    )
    await connection.execute(
        text(
            "INSERT INTO outbox (id, status, exchange, routing_key, event_key, payload) "
            "VALUES ('22222222-2222-4222-8222-222222222222', 'PENDING', 'payments', 'payments.new', 'k', '{}')",
        ),
    )

    run_alembic(migrations_db_url, "upgrade", "head")

    payment = (
        await connection.execute(text("SELECT amount, webhook_attempts, webhook_delivered_at FROM payments"))
    ).one()
    assert (str(payment.amount), payment.webhook_attempts, payment.webhook_delivered_at) == ("100.50", 0, None)
    assert (await connection.execute(text("SELECT headers FROM outbox"))).scalar_one() == {}

    run_alembic(migrations_db_url, "downgrade", FIRST_REVISION)

    assert (await connection.execute(text("SELECT count(*) FROM payments"))).scalar_one() == 1
    assert (await connection.execute(text("SELECT count(*) FROM outbox"))).scalar_one() == 1
    run_alembic(migrations_db_url, "upgrade", "head")
