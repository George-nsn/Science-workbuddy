from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    BrainstormSessionMemoryResponse,
    MemoryContextItemResponse,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryRecallItemResponse,
    MemoryRecallRequest,
    MemoryRecallResponse,
    ProjectFactRelationRequest,
    ProjectFactRelationResponse,
    ProjectFactResponse,
    ProjectFactsResponse,
    ProjectFactStatusRequest,
    RecycleBinResponse,
    RetentionPolicyRequest,
    RetentionPolicyResponse,
    TrashItemResponse,
)
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import Project, ProjectFact
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.memory_context import MemoryContextService
from science_buddy.services.memory_lifecycle import (
    MemoryLifecycleService,
    TrashEntityType,
)
from science_buddy.services.memory_retrieval import (
    HybridMemoryRetrievalService,
    MemoryRecallFilters,
)
from science_buddy.services.project_memory import ProjectMemoryService

router = APIRouter(prefix="/memory", tags=["memory"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/projects/{project_id}/recall",
    response_model=MemoryRecallResponse,
)
async def recall_project_memory(
    project_id: UUID,
    payload: MemoryRecallRequest,
    session: SessionDependency,
) -> MemoryRecallResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    result = await HybridMemoryRetrievalService(
        session,
        embedding_service=get_embedding_service(),
    ).recall(
        project_id=project_id,
        query=payload.query,
        filters=MemoryRecallFilters(
            entity_types=tuple(payload.entity_types),
            categories=tuple(payload.categories),
            statuses=tuple(payload.statuses),
            min_confidence=payload.min_confidence,
            created_after=payload.created_after,
        ),
        limit=payload.limit,
    )
    await session.commit()
    return MemoryRecallResponse(
        project_id=project_id,
        strategy=result.strategy,
        indexed=result.indexed,
        embedding_model=result.embedding_model,
        items=[
            MemoryRecallItemResponse(
                entity_type=value.entity_type,
                entity_id=value.entity_id,
                text=value.text,
                category=value.category,
                status=value.status,
                confidence=value.confidence,
                importance=value.importance,
                created_at=value.created_at,
                dense_score=value.dense_score,
                lexical_score=value.lexical_score,
                recent_score=value.recent_score,
                importance_score=value.importance_score,
                fused_score=value.fused_score,
                routes=list(value.routes),
                source=value.source,
                related=list(value.related),
            )
            for value in result.items
        ],
    )


def _fact_response(value: ProjectFact) -> ProjectFactResponse:
    return ProjectFactResponse(
        id=value.id,
        project_id=value.project_id,
        category=value.category,
        statement=value.statement,
        source_type=value.source_type,
        source_id=value.source_id,
        source_session_id=value.source_session_id,
        source_locator=value.source_locator,
        confidence=value.confidence,
        importance=value.importance,
        status=cast(Literal["active", "superseded", "retracted"], value.status),
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


@router.get("/projects/{project_id}/retention", response_model=RetentionPolicyResponse)
async def get_retention(project_id: UUID, session: SessionDependency) -> RetentionPolicyResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return RetentionPolicyResponse(
        project_id=project.id,
        trash_retention_days=project.trash_retention_days,
    )


@router.put("/projects/{project_id}/retention", response_model=RetentionPolicyResponse)
async def update_retention(
    project_id: UUID,
    payload: RetentionPolicyRequest,
    session: SessionDependency,
) -> RetentionPolicyResponse:
    try:
        project = await MemoryLifecycleService(session).set_retention(
            project_id,
            payload.trash_retention_days,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RetentionPolicyResponse(
        project_id=project.id,
        trash_retention_days=project.trash_retention_days,
    )


@router.get("/projects/{project_id}/trash", response_model=RecycleBinResponse)
async def get_recycle_bin(project_id: UUID, session: SessionDependency) -> RecycleBinResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    records = await MemoryLifecycleService(session).list_trash(project_id)
    return RecycleBinResponse(
        project_id=project_id,
        retention_days=project.trash_retention_days,
        items=[
            TrashItemResponse(
                entity_type=record.entity_type,
                entity_id=record.entity_id,
                title=record.title,
                deleted_at=record.deleted_at,
                purge_after=record.purge_after,
            )
            for record in records
        ],
    )


@router.post("/projects/{project_id}/trash/{entity_type}/{entity_id}/restore", status_code=204)
async def restore_recycle_bin_item(
    project_id: UUID,
    entity_type: TrashEntityType,
    entity_id: UUID,
    session: SessionDependency,
) -> Response:
    try:
        await MemoryLifecycleService(session).restore(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.delete("/projects/{project_id}/trash/{entity_type}/{entity_id}", status_code=204)
async def purge_recycle_bin_item(
    project_id: UUID,
    entity_type: TrashEntityType,
    entity_id: UUID,
    session: SessionDependency,
) -> Response:
    try:
        await MemoryLifecycleService(session).purge(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/projects/{project_id}/facts", response_model=ProjectFactsResponse)
async def get_project_facts(
    project_id: UUID,
    session: SessionDependency,
    category: Annotated[str | None, Query(max_length=48)] = None,
    status: Annotated[Literal["active", "superseded", "retracted"], Query()] = "active",
) -> ProjectFactsResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    facts = await ProjectMemoryService(session).list_facts(
        project_id,
        category=category,
        status=status,
        limit=200,
    )
    return ProjectFactsResponse(
        project_id=project_id,
        facts=[_fact_response(value) for value in facts],
    )


@router.patch("/projects/{project_id}/facts/{fact_id}", response_model=ProjectFactResponse)
async def update_project_fact(
    project_id: UUID,
    fact_id: UUID,
    payload: ProjectFactStatusRequest,
    session: SessionDependency,
) -> ProjectFactResponse:
    try:
        fact = await ProjectMemoryService(session).update_fact_status(
            project_id=project_id,
            fact_id=fact_id,
            status=payload.status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _fact_response(fact)


@router.post(
    "/projects/{project_id}/facts/{fact_id}/relations",
    response_model=ProjectFactRelationResponse,
)
async def relate_project_facts(
    project_id: UUID,
    fact_id: UUID,
    payload: ProjectFactRelationRequest,
    session: SessionDependency,
) -> ProjectFactRelationResponse:
    try:
        relation = await ProjectMemoryService(session).relate_facts(
            project_id=project_id,
            source_fact_id=fact_id,
            target_fact_id=payload.target_fact_id,
            relation_type=payload.relation_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProjectFactRelationResponse(
        id=relation.id,
        project_id=relation.project_id,
        source_fact_id=relation.source_fact_id,
        target_fact_id=relation.target_fact_id,
        relation_type=cast(
            Literal["conflicts_with", "supersedes", "synonym_of"],
            relation.relation_type,
        ),
        provenance=relation.provenance,
        created_at=relation.created_at,
    )


@router.get(
    "/brainstorm/sessions/{session_id}/summary",
    response_model=BrainstormSessionMemoryResponse,
)
async def get_session_memory(
    session_id: UUID,
    session: SessionDependency,
) -> BrainstormSessionMemoryResponse:
    memory = await ProjectMemoryService(session).latest_session_memory(session_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Session summary not found")
    return BrainstormSessionMemoryResponse(
        session_id=memory.session_id,
        source_message_id=memory.source_message_id,
        turn_number=memory.turn_number,
        summary_markdown=memory.summary_markdown,
        summary_data=memory.summary_data,
        updated_at=memory.updated_at,
    )


@router.post(
    "/projects/{project_id}/context",
    response_model=MemoryContextResponse,
)
async def recall_project_memory_context(
    project_id: UUID,
    payload: MemoryContextRequest,
    session: SessionDependency,
) -> MemoryContextResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        result = await MemoryContextService(session).recall(
            project_id=project_id,
            query=payload.query,
            consumer=payload.consumer,
            memory_types=tuple(payload.memory_types) or None,
            categories=tuple(payload.categories),
            statuses=tuple(payload.statuses),
            min_confidence=payload.min_confidence,
            created_after=payload.created_after,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    response = result.to_payload()
    return MemoryContextResponse(
        project_id=project_id,
        consumer=result.consumer,
        allowed_memory_types=list(result.allowed_memory_types),
        strategy=result.strategy,
        indexed=result.indexed,
        embedding_model=result.embedding_model,
        evidence_boundary=str(response["evidence_boundary"]),
        items=[
            MemoryContextItemResponse.model_validate(value)
            for value in response["items"]
        ],
    )