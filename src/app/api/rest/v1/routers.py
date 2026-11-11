from fastapi import APIRouter, Depends, status

from app.api.rest.dependencies.auth import require_api_key
from app.api.rest.schemas import DetailErrorSchema
from app.api.rest.v1 import payments

v1_router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(require_api_key)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": DetailErrorSchema},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": DetailErrorSchema},
    },
)
v1_router.include_router(payments.router)
