from fastapi import status
from httpx import ASGITransport, AsyncClient
from pytest_mock import MockerFixture

from app.api.rest.main import app
from app.app_layer.interfaces.services.health.services import AbstractHealthService
from app.containers import Container
from app.infra.repositories.sqla.health import HealthRepository


async def test_healthcheck_ok_without_api_key(client: AsyncClient) -> None:
    client.headers.pop("X-API-Key")

    response = await client.get("/healthcheck")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


async def test_healthcheck_database_down_unavailable(client: AsyncClient, mocker: MockerFixture) -> None:
    mocker.patch.object(HealthRepository, "ping_database", side_effect=ConnectionError)

    response = await client.get("/healthcheck")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


async def test_unhandled_error_returns_unknown_error(container: Container, mocker: MockerFixture) -> None:
    service = mocker.AsyncMock(spec=AbstractHealthService)
    service.process.side_effect = RuntimeError("boom")
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with container.health_service.override(service):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthcheck")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["detail"]["message_code"] == "UNKNOWN_ERROR"
