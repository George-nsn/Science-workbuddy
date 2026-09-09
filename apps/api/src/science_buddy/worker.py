from typing import Any
from uuid import UUID

from arq.connections import RedisSettings
from arq.cron import cron

from science_buddy.config import get_settings
from science_buddy.infrastructure.database import async_session_factory, engine
from science_buddy.services.embeddings import (
    SentenceTransformerEmbeddingService,
    get_embedding_service,
    index_collection_chunks,
    index_project_chunks,
)
from science_buddy.services.memory_lifecycle import run_memory_maintenance


async def startup(ctx: dict[str, Any]) -> None:
    ctx["service"] = "science-buddy-worker"
    ctx["embedding_service"] = get_embedding_service()


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx.clear()
    await engine.dispose()


async def baseline_health_task(ctx: dict[str, Any]) -> dict[str, str]:
    """A deterministic worker smoke task; it does not ingest or parse documents."""
    return {"status": "ok", "service": str(ctx.get("service", "unknown"))}


async def embed_project_chunks(ctx: dict[str, Any], project_id: str) -> dict[str, int | str]:
    service = ctx.get("embedding_service")
    if not isinstance(service, SentenceTransformerEmbeddingService):
        raise RuntimeError("Embedding service was not initialized")
    async with async_session_factory() as session:
        indexed = await index_project_chunks(
            session,
            service,
            project_id=UUID(project_id),
        )
    return {"project_id": project_id, "indexed_chunks": indexed}


async def embed_collection_chunks(
    ctx: dict[str, Any], collection_id: str
) -> dict[str, int | str]:
    service = ctx.get("embedding_service")
    if not isinstance(service, SentenceTransformerEmbeddingService):
        raise RuntimeError("Embedding service was not initialized")
    async with async_session_factory() as session:
        indexed = await index_collection_chunks(
            session,
            service,
            collection_id=UUID(collection_id),
        )
    return {"collection_id": collection_id, "indexed_chunks": indexed}


async def memory_maintenance_task(_ctx: dict[str, Any]) -> dict[str, int]:
    result = await run_memory_maintenance(async_session_factory)
    return {
        "cache_entries_deleted": result.cache_entries_deleted,
        "trash_items_purged": result.trash_items_purged,
    }


class WorkerSettings:
    functions = [
        baseline_health_task,
        embed_project_chunks,
        embed_collection_chunks,
        memory_maintenance_task,
    ]
    cron_jobs = [cron("science_buddy.worker.memory_maintenance_task", minute=17)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Serialize SQLite-writing jobs to avoid lock contention on a local single-user store.
    max_jobs = 1
    job_timeout = 1800
    max_tries = 2
    keep_result = 3600
