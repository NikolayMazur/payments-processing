from fastapi import APIRouter, FastAPI

from app.api.rest import healthcheck
from app.api.rest.v1.routers import v1_router
from app.config import config


def init_rest_api(app: FastAPI) -> None:
    api_router = APIRouter(prefix=config.API.ROOT_PREFIX)
    api_router.include_router(v1_router)

    app.include_router(healthcheck.router)
    app.include_router(api_router)
