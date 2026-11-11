import asyncio

import typer
import uvicorn

from app.api.cli import outbox
from app.api.cli.outbox import run_outbox_relay as run_outbox_relay_command
from app.config import config
from app.containers import Container
from app.logs import get_logging_config, init_logging

app = typer.Typer()


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
    init_logging(config)
    container = Container()
    container.wire(modules=[outbox])
    asyncio.run(run_outbox_relay_command(container))


if __name__ == "__main__":
    app()
