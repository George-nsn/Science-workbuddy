from dataclasses import dataclass
from typing import Any
from uuid import UUID

from science_buddy.domain.contracts import RetrievalCandidate, RetrievalRouteTrace
from science_buddy.services.cache import ThreeLevelCache
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.query_planning import QueryPlan
from science_buddy.services.retrieval import (
    RetrievalConfig,
    RetrievalReport,
    SQLiteHybridRetriever,
)


@dataclass(frozen=True, slots=True)
class RetrievalExecution:
    candidates: list[RetrievalCandidate]
    report: dict[str, Any]
    cache_level: str


def retrieval_report_payload(report: RetrievalReport) -> dict[str, Any]:
    return {
        "version": report.version,
        "query_language": report.query_language,
        "original_query": report.original_query,
        "english_query": report.english_query,
        "expansions": [list(value) for value in report.expansions],
        "routes": [
            {
                "route": route.route,
                "candidates": len(route.candidates),
                "elapsed_ms": route.elapsed_ms,
                "error": route.error,
                "metrics": dict(route.metrics),
            }
            for route in report.routes
        ],
        "anchors": report.anchors,
        "evidence_blocks": report.evidence_blocks,
        "elapsed_ms": report.elapsed_ms,
        "reranker": {
            "enabled": report.reranker.enabled,
            "applied": report.reranker.applied,
            "model": report.reranker.model,
            "candidates": report.reranker.candidates,
            "elapsed_ms": report.reranker.elapsed_ms,
            "error": report.reranker.error,
        },
        "excluded_retracted": report.excluded_retracted,
    }


async def retrieve_without_cache(
    *,
    retriever: SQLiteHybridRetriever,
    cache: ThreeLevelCache,
    token_service: EvidenceTokenService,
    query_plan: QueryPlan,
    workflow_id: UUID,
    project_id: UUID,
    collection_id: UUID | None,
    project_revision: int,
    limit: int,
    config: RetrievalConfig,
) -> RetrievalExecution:
    """Retrieve in the caller transaction without touching independent cache writers."""
    _ = (cache, token_service, workflow_id, collection_id, project_revision, config)
    candidates = await retriever.retrieve(
        query_plan.original_query,
        project_id=project_id,
        limit=limit,
        query_plan=query_plan,
    )
    report = retriever.last_report
    if report is None:
        raise RuntimeError("Retrieval report was not generated")
    return RetrievalExecution(
        candidates=candidates,
        report=retrieval_report_payload(report),
        cache_level="miss",
    )


def retrieval_config_payload(config: RetrievalConfig) -> dict[str, Any]:
    return {
        "version": config.version,
        "rrf_k": config.rrf_k,
        "weights": dict(config.weights),
        "top_k_dense": config.top_k_dense,
        "top_k_fts": config.top_k_fts,
        "top_k_simple": config.top_k_simple,
        "top_k_metadata": config.top_k_metadata,
        "fused_pool": config.fused_pool,
        "max_chunks_per_paper": config.max_chunks_per_paper,
        "context_radius": config.context_radius,
        "context_max_chars": config.context_max_chars,
        "route_timeout_seconds": config.route_timeout_seconds,
        "dense_route_timeout_seconds": config.dense_route_timeout_seconds,
        "graph_seed_papers": config.graph_seed_papers,
        "graph_neighbors": config.graph_neighbors,
        "graph_hops": config.graph_hops,
        "graph_community_top_k": config.graph_community_top_k,
        "graph_path_top_k": config.graph_path_top_k,
        "reranker_enabled": config.reranker_enabled,
        "reranker_model": config.reranker_model,
        "reranker_candidates": config.reranker_candidates,
        "reranker_weight": config.reranker_weight,
        "vector_backend": config.vector_backend,
        "subquery_parallelism": config.subquery_parallelism,
        "neighbor_query_filter": config.neighbor_query_filter,
        "neighbor_max_per_anchor": config.neighbor_max_per_anchor,
        "neighbor_min_score": config.neighbor_min_score,
        "exclude_retracted": config.exclude_retracted,
        "journal_prior_weight": config.journal_prior_weight,
    }


def retrieval_cache_key_payload(
    *,
    query_plan: QueryPlan,
    project_id: UUID,
    collection_id: UUID | None,
    project_revision: int,
    limit: int,
    config: RetrievalConfig,
) -> dict[str, Any]:
    return {
        "project_id": str(project_id),
        "project_revision": project_revision,
        "collection_id": str(collection_id) if collection_id else None,
        "query": query_plan.original_query,
        "english_query": query_plan.english_query,
        "simple_query": query_plan.simple_query,
        "dense_queries": [list(value) for value in query_plan.dense_queries],
        "expansions": [
            [item.source, item.target, item.authority]
            for item in query_plan.expansions
        ],
        "query_plan_version": query_plan.version,
        "routing_context": query_plan.routing_context,
        "research_question_type": query_plan.research_question_type,
        "query_focus": query_plan.query_focus,
        "parent_query": query_plan.parent_query,
        "subquery_id": query_plan.subquery_id,
        "limit": limit,
        "config": retrieval_config_payload(config),
    }


async def retrieve_with_cache(
    *,
    retriever: SQLiteHybridRetriever,
    cache: ThreeLevelCache,
    token_service: EvidenceTokenService,
    query_plan: QueryPlan,
    workflow_id: UUID,
    project_id: UUID,
    collection_id: UUID | None,
    project_revision: int,
    limit: int,
    config: RetrievalConfig,
) -> RetrievalExecution:
    key_payload = retrieval_cache_key_payload(
        query_plan=query_plan,
        project_id=project_id,
        collection_id=collection_id,
        project_revision=project_revision,
        limit=limit,
        config=config,
    )
    key = cache.digest(key_payload)
    namespace = f"retrieval:{project_id}"
    hit = await cache.get(namespace, key)
    if hit is not None:
        candidates = [
            RetrievalCandidate(
                chunk_id=UUID(item["chunk_id"]),
                evidence_id=token_service.issue(
                    workflow_id,
                    UUID(item["chunk_id"]),
                ),
                text=item["text"],
                score=float(item["score"]),
                source_locator=dict(item["source_locator"]),
                paper_id=UUID(item["paper_id"]) if item.get("paper_id") else None,
                content_hash=item.get("content_hash"),
                traces=tuple(
                    RetrievalRouteTrace(
                        route=trace["route"],
                        rank=int(trace["rank"]),
                        raw_score=float(trace["raw_score"]),
                        weighted_rrf=float(trace["weighted_rrf"]),
                    )
                    for trace in item.get("traces", [])
                ),
                role=item.get("role", "anchor"),
                anchor_chunk_id=(
                    UUID(item["anchor_chunk_id"]) if item.get("anchor_chunk_id") else None
                ),
            )
            for item in hit.value["candidates"]
        ]
        return RetrievalExecution(
            candidates=candidates,
            report=dict(hit.value["report"]),
            cache_level=hit.level,
        )

    candidates = await retriever.retrieve(
        query_plan.original_query,
        project_id=project_id,
        limit=limit,
        query_plan=query_plan,
    )
    report = retriever.last_report
    if report is None:
        raise RuntimeError("Retrieval report was not generated")
    report_payload = retrieval_report_payload(report)
    value = {
        "report": report_payload,
        "candidates": [
            {
                "chunk_id": str(candidate.chunk_id),
                "text": candidate.text,
                "score": candidate.score,
                "source_locator": candidate.source_locator,
                "paper_id": str(candidate.paper_id) if candidate.paper_id else None,
                "content_hash": candidate.content_hash,
                "traces": [
                    {
                        "route": trace.route,
                        "rank": trace.rank,
                        "raw_score": trace.raw_score,
                        "weighted_rrf": trace.weighted_rrf,
                    }
                    for trace in candidate.traces
                ],
                "role": candidate.role,
                "anchor_chunk_id": (
                    str(candidate.anchor_chunk_id) if candidate.anchor_chunk_id else None
                ),
            }
            for candidate in candidates
        ],
    }
    await cache.set(namespace, key, value)
    return RetrievalExecution(candidates=candidates, report=report_payload, cache_level="miss")
