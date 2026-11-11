import asyncio
from typing import Annotated

import typer
import uvicorn

from app.api.cli import outbox
from app.api.cli.outbox import run_clean_outbox, run_requeue_dead_letters
from app.api.cli.outbox import run_outbox_relay as run_outbox_relay_command
from app.config import config
from app.containers import Container
from app.logs import get_logging_config, init_logging

app = typer.Typer()


def init_container() -> Container:
    init_logging(config)
    container = Container()
    container.wire(modules=[outbox])
    return container


@app.command(help="Запуск web-сервера с приложением.")
def run_server() -> None:
    uvicorn.run(
        "app.api.rest.main:app",
        host=config.UVICORN.HOST,
        port=config.UVICORN.PORT,
        workers=config.UVICORN.WORKERS,
        log_config=get_logging_config(config),
    )


@app.command(help="Запуск relay-воркера: публикует события из таблицы outbox в RabbitMQ.")
def run_outbox_relay() -> None:
    asyncio.run(run_outbox_relay_command(init_container()))


@app.command(help="Удаление опубликованных записей outbox старше срока хранения.")
def clean_outbox(
    retention_days: Annotated[
        int | None,
        typer.Option(min=0, help="Срок хранения в днях. По умолчанию OUTBOX__RETENTION_DAYS."),
    ] = None,
) -> None:
    deleted = asyncio.run(run_clean_outbox(init_container(), retention_days))
    typer.echo(f"Удалено записей outbox: {deleted}")


@app.command(help="Возврат сообщений из payments.new.dlq в обработку. Счётчик попыток обнуляется.")
def requeue_dlq(
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Сколько сообщений вернуть. По умолчанию все, что лежат в очереди."),
    ] = None,
) -> None:
    moved = asyncio.run(run_requeue_dead_letters(init_container(), limit))
    typer.echo(f"Возвращено сообщений в обработку: {moved}")


if __name__ == "__main__":
    app()
