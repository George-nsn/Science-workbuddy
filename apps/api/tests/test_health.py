import httpx
import pytest

from science_buddy.api import health
from science_buddy.main import app


@pytest.mark.asyncio
async def test_liveness() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "science-buddy-api", "checks": None}


@pytest.mark.asyncio
async def test_readiness_reports_dependency_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def ready() -> bool:
        return True

    async def unavailable(_request) -> bool:  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(health, "database_is_ready", ready)
    monkeypatch.setattr(health, "redis_is_ready", unavailable)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"] == {"database": True, "redis": False}
