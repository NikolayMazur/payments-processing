from app.config import DatabaseSettings
from tests.conftest import run_alembic


def test_models_match_migrations(db_settings: DatabaseSettings) -> None:
    result = run_alembic(db_settings.URL, "check")

    assert "No new upgrade operations detected" in result.stdout + result.stderr


def test_single_head(db_settings: DatabaseSettings) -> None:
    result = run_alembic(db_settings.URL, "heads")

    assert len(result.stdout.strip().splitlines()) == 1


def test_downgrade_and_upgrade(db_settings: DatabaseSettings) -> None:
    run_alembic(db_settings.URL, "downgrade", "base")
    run_alembic(db_settings.URL, "upgrade", "head")
