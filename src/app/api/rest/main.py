from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import rest
from app.api.rest.exception_handlers import init_exception_handlers
from app.api.rest.routers import init_rest_api
from app.config import config
from app.containers import Container, shutdown_container
from app.logs import init_logging

init_logging(config)


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    container = Container()
    container.wire(packages=[rest])
    app_.state.container = container
    yield
    await shutdown_container(container)


app = FastAPI(
    title="Payments processing",
    description="Асинхронный сервис процессинга платежей",
    lifespan=lifespan,
    docs_url="/docs" if config.API.DOCS_ENABLED else None,
    redoc_url=None,
    openapi_url="/openapi.json" if config.API.DOCS_ENABLED else None,
)
init_exception_handlers(app)
init_rest_api(app)
