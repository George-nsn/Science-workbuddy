from typing import Annotated
from uuid import UUID

from arq.connections import RedisSettings, create_pool
from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    RagCollectionCreateRequest,
    RagCollectionResponse,
    RagCollectionsResponse,
)
from science_buddy.config import Settings, get_settings
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import RagCollection
from science_buddy.services.cache import ThreeLevelCache
from science_buddy.services.collections import RagCollectionService
from science_buddy.services.knowledge_graph import KnowledgeGraphBuilder

router = APIRouter(prefix="/rag", tags=["rag-collections"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


async def _enqueue_vector(collection: RagCollection, settings: Settings) -> str | None:
    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            job = await pool.enqueue_job("embed_collection_chunks", str(collection.id))
            return job.job_id if job else None
        finally:
            await pool.aclose()
    except (OSError, RedisError):
        return None


@router.post("/collections", response_model=RagCollectionResponse)
async def create_collection(
    payload: RagCollectionCreateRequest,
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> RagCollectionResponse:
    service = RagCollectionService(session)
    try:
        collection = await service.create(
            project_id=payload.project_id,
            paper_ids=payload.paper_ids,
            name=payload.name,
            build_vector=payload.build_vector,
            build_graph=payload.build_graph,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    paper_ids = await service.paper_ids(collection.id)
    warning: str | None = None
    if payload.build_graph:
        try:
            await KnowledgeGraphBuilder(session).rebuild(
                payload.project_id,
                paper_ids=paper_ids,
                scope_key=f"collection:{collection.id}",
            )
            reloaded = await session.get(RagCollection, collection.id)
            if reloaded is not None:
                collection = reloaded
                collection.graph_status = "ready"
                await session.commit()
        except ValueError as exc:
            reloaded = await session.get(RagCollection, collection.id)
            if reloaded is not None:
                collection = reloaded
                collection.graph_status = "failed"
                await session.commit()
            warning = str(exc)
    if payload.build_vector:
        reloaded = await session.get(RagCollection, collection.id)
        if reloaded is None:
            raise HTTPException(status_code=500, detail="Collection disappeared")
        collection = reloaded
        job_id = await _enqueue_vector(collection, settings)
        if job_id:
            collection.vector_status = "queued"
            collection.vector_job_id = job_id
        else:
            collection.vector_status = "pending"
            warning = (
                f"{warning}; " if warning else ""
            ) + "Redis is unavailable; vector indexing remains pending"
        await session.commit()
    cache: ThreeLevelCache = request.app.state.cache
    await cache.invalidate_namespace(f"retrieval:{payload.project_id}")
    reloaded = await session.get(RagCollection, collection.id)
    if reloaded is None:
        raise HTTPException(status_code=500, detail="Collection disappeared")
    collection = reloaded
    return RagCollectionResponse(
        id=collection.id,
        project_id=collection.project_id,
        name=collection.name,
        paper_count=len(paper_ids),
        vector_status=collection.vector_status,
        graph_status=collection.graph_status,
        vector_job_id=collection.vector_job_id,
        warning=warning,
    )


@router.get("/collections", response_model=RagCollectionsResponse)
async def list_collections(
    project_id: UUID,
    session: SessionDependency,
) -> RagCollectionsResponse:
    summaries = await RagCollectionService(session).summaries(project_id)
    return RagCollectionsResponse(
        project_id=project_id,
        collections=[
            RagCollectionResponse(
                id=item.collection.id,
                project_id=item.collection.project_id,
                name=item.collection.name,
                paper_count=item.paper_count,
                vector_status=item.collection.vector_status,
                graph_status=item.collection.graph_status,
                vector_job_id=item.collection.vector_job_id,
            )
            for item in summaries
        ],
    )


@router.post("/collections/{collection_id}/vector", response_model=RagCollectionResponse)
async def retry_collection_vector(
    collection_id: UUID,
    session: SessionDependency,
    settings: SettingsDependency,
) -> RagCollectionResponse:
    collection = await session.get(RagCollection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="RAG collection not found")
    job_id = await _enqueue_vector(collection, settings)
    warning = None
    if job_id:
        collection.vector_status = "queued"
        collection.vector_job_id = job_id
    else:
        collection.vector_status = "pending"
        warning = "Redis is unavailable; vector indexing remains pending"
    await session.commit()
    paper_count = len(await RagCollectionService(session).paper_ids(collection_id))
    return RagCollectionResponse(
        id=collection.id,
        project_id=collection.project_id,
        name=collection.name,
        paper_count=paper_count,
        vector_status=collection.vector_status,
        graph_status=collection.graph_status,
        vector_job_id=collection.vector_job_id,
        warning=warning,
    )
