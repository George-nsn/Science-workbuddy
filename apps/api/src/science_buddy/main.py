from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from science_buddy.api.router import api_router
from science_buddy.config import get_settings
from science_buddy.infrastructure.database import async_session_factory, engine
from science_buddy.logging import configure_logging
from science_buddy.services.background_tasks import (
    InProcessTaskManager,
    recover_interrupted_background_jobs,
)
from science_buddy.services.cache import ThreeLevelCache

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    await recover_interrupted_background_jobs(async_session_factory)
    application.state.redis = Redis.from_url(settings.redis_url)
    application.state.background_tasks = InProcessTaskManager()
    application.state.cache = ThreeLevelCache(
        redis=application.state.redis,
        session_factory=async_session_factory,
        namespace=settings.cache_namespace,
        l1_max_entries=settings.cache_l1_max_entries,
        l1_ttl_seconds=settings.cache_l1_ttl_seconds,
        l2_ttl_seconds=settings.cache_l2_ttl_seconds,
        l3_ttl_seconds=settings.cache_l3_ttl_seconds,
    )
    yield
    await application.state.background_tasks.shutdown()
    await application.state.redis.aclose()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Local-first biomedical literature research assistance API. "
        "Not intended for diagnosis, prescribing, or clinical decision-making."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
)
app.include_router(api_router, prefix=settings.api_prefix)
