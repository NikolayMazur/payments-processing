from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.rest.schemas import DetailErrorSchema, HealthCheckSchema
from app.app_layer.interfaces.services.health.exceptions import DatabaseConnectionError
from app.app_layer.interfaces.services.health.services import AbstractHealthService
from app.containers import Container
from app.errors import SERVICE_UNAVAILABLE_ERROR

router = APIRouter(tags=["Service"])


@router.get(
    "/healthcheck",
    response_model=HealthCheckSchema,
    name="healthcheck",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": DetailErrorSchema}},
)
@inject
async def healthcheck(
    service: Annotated[AbstractHealthService, Depends(Provide[Container.health_service])],
) -> HealthCheckSchema:
    try:
        await service.process()
    except DatabaseConnectionError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_UNAVAILABLE_ERROR)
    return HealthCheckSchema()
