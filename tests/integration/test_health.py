import logging

import pytest

from tests.factories import api_client
from tests.fakes import CountingTransformer

pytestmark = pytest.mark.integration

# Nothing listens on port 1, so connecting fails immediately rather than timing out.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://user:pw@127.0.0.1:1/cache"


@pytest.mark.usefixtures("engine")
async def test_health_ok(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.usefixtures("engine")
async def test_health_answers_head_requests(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        response = await client.head("/health")

    assert response.status_code == 200


async def test_health_db_down_returns_503(caplog: pytest.LogCaptureFixture) -> None:
    async with api_client(UNREACHABLE_DATABASE_URL, CountingTransformer()) as client:
        with caplog.at_level(logging.WARNING, logger="caching_service.services.health"):
            response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert [r.getMessage() for r in caplog.records] == ["database health check failed"]


@pytest.mark.usefixtures("engine")
async def test_health_documents_both_outcomes(database_url: str) -> None:
    async with api_client(database_url, CountingTransformer()) as client:
        schema = (await client.get("/openapi.json")).json()

    assert {"200", "503"} <= set(schema["paths"]["/health"]["get"]["responses"])
