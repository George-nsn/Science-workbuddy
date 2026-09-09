from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    GraphBuildRequest,
    GraphBuildResponse,
    GraphCommunityReportResponse,
    GraphCommunityResponse,
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphRagHitResponse,
    GraphRagQueryRequest,
    GraphRagQueryResponse,
    GraphSnapshotResponse,
)
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import (
    GraphCommunity,
    GraphCommunityReport,
    GraphEdge,
    GraphNode,
    Project,
    RagCollection,
)
from science_buddy.services.cache import ThreeLevelCache
from science_buddy.services.collections import RagCollectionService
from science_buddy.services.graphrag import FullGraphRagRetriever
from science_buddy.services.knowledge_graph import KnowledgeGraphBuilder

router = APIRouter(prefix="/graph", tags=["knowledge-graph"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.post("/rebuild", response_model=GraphBuildResponse)
async def rebuild_graph(
    payload: GraphBuildRequest,
    request: Request,
    session: SessionDependency,
) -> GraphBuildResponse:
    try:
        paper_ids = None
        scope_key = "project"
        if payload.collection_id:
            collection = await session.get(RagCollection, payload.collection_id)
            if collection is None or collection.project_id != payload.project_id:
                raise ValueError("RAG collection does not belong to the project")
            paper_ids = await RagCollectionService(session).paper_ids(collection.id)
            scope_key = f"collection:{collection.id}"
        result = await KnowledgeGraphBuilder(session).rebuild(
            payload.project_id,
            paper_ids=paper_ids,
            scope_key=scope_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    cache: ThreeLevelCache = request.app.state.cache
    await cache.invalidate_namespace(f"retrieval:{payload.project_id}")
    return GraphBuildResponse(
        project_id=result.project_id,
        nodes=result.nodes,
        edges=result.edges,
        revision=result.revision,
        scope_key=result.scope_key,
        communities=result.communities,
        reports=result.reports,
        mentions=result.mentions,
    )


@router.get("/{project_id}", response_model=GraphSnapshotResponse)
async def graph_snapshot(
    project_id: UUID,
    session: SessionDependency,
    node_key: str | None = None,
    collection_id: UUID | None = None,
    limit: int = 200,
) -> GraphSnapshotResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    bounded_limit = min(max(limit, 1), 500)
    scope_key = f"collection:{collection_id}" if collection_id else "project"
    edge_statement = select(GraphEdge).where(
        GraphEdge.project_id == project_id,
        GraphEdge.scope_key == scope_key,
    )
    if node_key:
        edge_statement = edge_statement.where(
            or_(GraphEdge.source_key == node_key, GraphEdge.target_key == node_key)
        )
    edges = list((await session.scalars(edge_statement.limit(bounded_limit))).all())
    keys = {key for edge in edges for key in (edge.source_key, edge.target_key)}
    node_statement = select(GraphNode).where(
        GraphNode.project_id == project_id,
        GraphNode.scope_key == scope_key,
    )
    if keys:
        node_statement = node_statement.where(GraphNode.node_key.in_(keys))
    nodes = list((await session.scalars(node_statement.limit(bounded_limit))).all())
    communities = list(
        (
            await session.scalars(
                select(GraphCommunity)
                .where(
                    GraphCommunity.project_id == project_id,
                    GraphCommunity.scope_key == scope_key,
                )
                .order_by(GraphCommunity.level.desc(), GraphCommunity.community_key)
                .limit(bounded_limit)
            )
        ).all()
    )
    reports = list(
        (
            await session.scalars(
                select(GraphCommunityReport)
                .join(
                    GraphCommunity,
                    GraphCommunity.id == GraphCommunityReport.community_id,
                )
                .where(
                    GraphCommunity.project_id == project_id,
                    GraphCommunity.scope_key == scope_key,
                )
                .order_by(GraphCommunity.level.desc(), GraphCommunity.community_key)
                .limit(bounded_limit)
            )
        ).all()
    )
    return GraphSnapshotResponse(
        project_id=project_id,
        scope_key=scope_key,
        nodes=[
            GraphNodeResponse(
                node_key=node.node_key,
                node_type=node.node_type,
                label=node.label,
                properties=node.properties,
            )
            for node in nodes
        ],
        edges=[
            GraphEdgeResponse(
                source_key=edge.source_key,
                relation=edge.relation,
                target_key=edge.target_key,
                provenance=edge.provenance,
                confidence=edge.confidence,
            )
            for edge in edges
        ],
        communities=[
            GraphCommunityResponse(
                id=value.id,
                community_key=value.community_key,
                level=value.level,
                parent_community_id=value.parent_community_id,
                title=value.title,
                member_count=value.member_count,
                algorithm=value.algorithm,
                stats=value.stats,
            )
            for value in communities
        ],
        reports=[
            GraphCommunityReportResponse(
                id=value.id,
                community_id=value.community_id,
                title=value.title,
                summary=value.summary,
                report_text=value.report_text,
                evidence_bundle=value.evidence_bundle,
                generated_by=value.generated_by,
            )
            for value in reports
        ],
    )


@router.post("/query", response_model=GraphRagQueryResponse)
async def query_graph_rag(
    payload: GraphRagQueryRequest,
    session: SessionDependency,
) -> GraphRagQueryResponse:
    if await session.get(Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scope_key = "project"
    if payload.collection_id:
        collection = await session.get(RagCollection, payload.collection_id)
        if collection is None or collection.project_id != payload.project_id:
            raise HTTPException(status_code=404, detail="RAG collection not found in project")
        scope_key = f"collection:{collection.id}"
    result = await FullGraphRagRetriever(session).search(
        mode=payload.mode,
        query=payload.query,
        project_id=payload.project_id,
        scope_key=scope_key,
        seed_paper_ids=set(payload.seed_paper_ids),
        limit=payload.limit,
        hops=payload.hops,
        persist_paths=payload.persist_path_audit,
    )
    return GraphRagQueryResponse(
        project_id=payload.project_id,
        scope_key=scope_key,
        mode=payload.mode,
        hits=[
            GraphRagHitResponse(
                chunk_id=value.chunk_id,
                paper_id=value.paper_id,
                score=value.score,
                provenance=dict(value.provenance),
            )
            for value in result.hits
        ],
        metrics=dict(result.metrics),
        paths=[dict(value) for value in result.paths],
    )
