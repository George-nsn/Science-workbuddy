from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from science_buddy.infrastructure.database import engine

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    service: str
    checks: dict[str, bool] | None = None


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok", service="science-buddy-api")


async def database_is_ready() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def redis_is_ready(request: Request) -> bool:
    try:
        return bool(await request.app.state.redis.ping())
    except Exception:
        return False


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    checks = {
        "database": await database_is_ready(),
        "redis": await redis_is_ready(request),
    }
    status: Literal["ok", "not_ready"] = "ok" if all(checks.values()) else "not_ready"
    return HealthResponse(status=status, service="science-buddy-api", checks=checks)
