from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

import manage

runner = CliRunner()


@pytest.fixture()
def container(mocker: MockerFixture) -> MagicMock:
    """Команды не должны поднимать настоящий контейнер: здесь проверяется только разбор аргументов и вывод."""
    return mocker.patch.object(manage, "init_container").return_value


def test_clean_outbox_uses_default_retention(container: MagicMock, mocker: MockerFixture) -> None:
    run: AsyncMock = mocker.patch.object(manage, "run_clean_outbox", new_callable=mocker.AsyncMock, return_value=5)

    result = runner.invoke(manage.app, ["clean-outbox"])

    assert result.exit_code == 0
    assert "Удалено записей outbox: 5" in result.output
    run.assert_awaited_once_with(container, None)


def test_clean_outbox_passes_retention_days(container: MagicMock, mocker: MockerFixture) -> None:
    run: AsyncMock = mocker.patch.object(manage, "run_clean_outbox", new_callable=mocker.AsyncMock, return_value=0)

    result = runner.invoke(manage.app, ["clean-outbox", "--retention-days", "30"])

    assert result.exit_code == 0
    run.assert_awaited_once_with(container, 30)


def test_requeue_dlq_passes_limit(container: MagicMock, mocker: MockerFixture) -> None:
    run: AsyncMock = mocker.patch.object(
        manage,
        "run_requeue_dead_letters",
        new_callable=mocker.AsyncMock,
        return_value=2,
    )

    result = runner.invoke(manage.app, ["requeue-dlq", "--limit", "2"])

    assert result.exit_code == 0
    assert "Возвращено сообщений в обработку: 2" in result.output
    run.assert_awaited_once_with(container, 2)


@pytest.mark.parametrize(
    "args",
    [["requeue-dlq", "--limit", "0"], ["clean-outbox", "--retention-days", "-1"], ["requeue-dlq", "--limit", "many"]],
)
def test_invalid_arguments_rejected(container: MagicMock, mocker: MockerFixture, args: list[str]) -> None:
    clean: AsyncMock = mocker.patch.object(manage, "run_clean_outbox", new_callable=mocker.AsyncMock)
    requeue: AsyncMock = mocker.patch.object(manage, "run_requeue_dead_letters", new_callable=mocker.AsyncMock)

    result = runner.invoke(manage.app, args)

    assert result.exit_code == 2
    clean.assert_not_awaited()
    requeue.assert_not_awaited()


def test_run_server_starts_uvicorn_with_app(mocker: MockerFixture) -> None:
    run: MagicMock = mocker.patch.object(manage.uvicorn, "run")

    result = runner.invoke(manage.app, ["run-server"])

    assert result.exit_code == 0
    assert run.call_args.args == ("app.api.rest.main:app",)
    assert run.call_args.kwargs["port"] == manage.config.UVICORN.PORT


def test_run_outbox_relay_starts_relay(container: MagicMock, mocker: MockerFixture) -> None:
    run: AsyncMock = mocker.patch.object(manage, "run_outbox_relay_command", new_callable=mocker.AsyncMock)

    result = runner.invoke(manage.app, ["run-outbox-relay"])

    assert result.exit_code == 0
    run.assert_awaited_once_with(container)
