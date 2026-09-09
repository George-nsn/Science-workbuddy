import asyncio
import logging
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Row
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from science_buddy.config import Settings
from science_buddy.domain.contracts import RetrievalCandidate, RetrievalRouteTrace
from science_buddy.infrastructure.models import (
    Chunk,
    CollectionPaper,
    DocumentAsset,
    JournalMetric,
    Paper,
    ProjectPaper,
    Section,
)
from science_buddy.services.embeddings import (
    EmbeddingUnavailableError,
    SentenceTransformerEmbeddingService,
)
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.graphrag import FullGraphRagRetriever
from science_buddy.services.knowledge_graph import citation_neighbor_papers
from science_buddy.services.query_planning import (
    DeterministicQueryPlanner,
    QueryPlan,
    expand_plan_with_mesh,
)
from science_buddy.services.reranking import (
    CrossEncoderReranker,
    RerankDocument,
    RerankerUnavailableError,
)
from science_buddy.services.vector_store import (
    VectorStore,
    VectorStoreError,
    get_vector_store,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    version: str
    rrf_k: int
    weights: Mapping[str, float]
    top_k_dense: int
    top_k_fts: int
    top_k_simple: int
    top_k_metadata: int
    fused_pool: int
    max_chunks_per_paper: int
    context_radius: int
    context_max_chars: int
    route_timeout_seconds: float
    graph_seed_papers: int
    graph_neighbors: int
    dense_route_timeout_seconds: float = 30.0
    graph_hops: int = 1
    graph_community_top_k: int = 5
    graph_path_top_k: int = 3
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_candidates: int = 30
    reranker_weight: float = 0.65
    reranker_timeout_seconds: float = 30.0
    query_mesh_expansion: bool = False
    query_mesh_max_terms: int = 4
    vector_backend: str = "numpy"
    subquery_parallelism: int = 1
    neighbor_query_filter: bool = True
    neighbor_max_per_anchor: int = 2
    neighbor_min_score: float = 0.02
    exclude_retracted: bool = True
    journal_prior_weight: float = 0.1

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetrievalConfig":
        return cls(
            version=settings.retrieval_version,
            rrf_k=settings.retrieval_rrf_k,
            weights={
                "exact": settings.retrieval_weight_exact,
                "simple": settings.retrieval_weight_simple,
                "dense_original": settings.retrieval_weight_dense_original,
                "fts_english": settings.retrieval_weight_fts_english,
                "dense_translated": settings.retrieval_weight_dense_translated,
                "metadata": settings.retrieval_weight_metadata,
                "mesh": settings.retrieval_weight_mesh,
                "graph": 0.0,
                "graph_local": settings.retrieval_weight_graph,
                "graph_global": settings.retrieval_weight_graph_global,
                "graph_drift": settings.retrieval_weight_graph_drift,
                "graph_path": settings.retrieval_weight_graph_path,
            },
            top_k_dense=settings.retrieval_top_k_dense,
            top_k_fts=settings.retrieval_top_k_fts,
            top_k_simple=settings.retrieval_top_k_simple,
            top_k_metadata=settings.retrieval_top_k_metadata,
            fused_pool=settings.retrieval_fused_pool,
            max_chunks_per_paper=settings.retrieval_max_chunks_per_paper,
            context_radius=settings.retrieval_context_radius,
            context_max_chars=settings.retrieval_context_max_chars,
            route_timeout_seconds=settings.retrieval_route_timeout_seconds,
            dense_route_timeout_seconds=settings.retrieval_dense_route_timeout_seconds,
            graph_seed_papers=settings.retrieval_graph_seed_papers,
            graph_neighbors=settings.retrieval_graph_neighbors,
            graph_hops=settings.retrieval_graph_hops,
            graph_community_top_k=settings.retrieval_graph_community_top_k,
            graph_path_top_k=settings.retrieval_graph_path_top_k,
            reranker_enabled=settings.retrieval_reranker_enabled,
            reranker_model=settings.retrieval_reranker_model,
            reranker_candidates=settings.retrieval_reranker_candidates,
            reranker_weight=settings.retrieval_reranker_weight,
            reranker_timeout_seconds=settings.retrieval_reranker_timeout_seconds,
            query_mesh_expansion=settings.retrieval_query_mesh_expansion,
            query_mesh_max_terms=settings.retrieval_query_mesh_max_terms,
            vector_backend=settings.retrieval_vector_backend,
            subquery_parallelism=settings.retrieval_subquery_parallelism,
            neighbor_query_filter=settings.retrieval_neighbor_query_filter,
            neighbor_max_per_anchor=settings.retrieval_neighbor_max_per_anchor,
            neighbor_min_score=settings.retrieval_neighbor_min_score,
            exclude_retracted=settings.retrieval_exclude_retracted,
            journal_prior_weight=settings.retrieval_journal_prior_weight,
        )


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk: Chunk
    paper_id: UUID
    raw_score: float
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RouteResult:
    route: str
    candidates: tuple[RankedChunk, ...]
    elapsed_ms: float
    error: str | None = None
    metrics: Mapping[str, str | int | float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RouteCandidateBatch:
    candidates: tuple[RankedChunk, ...]
    metrics: Mapping[str, str | int | float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FusedChunk:
    chunk_id: UUID
    score: float
    traces: tuple[RetrievalRouteTrace, ...]


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    version: str
    query_language: str
    original_query: str
    english_query: str | None
    expansions: tuple[tuple[str, str], ...]
    routes: tuple[RouteResult, ...]
    anchors: int
    evidence_blocks: int
    elapsed_ms: float
    reranker: "RerankerReport"
    excluded_retracted: int


@dataclass(frozen=True, slots=True)
class RerankerReport:
    enabled: bool
    applied: bool
    model: str | None
    candidates: int
    elapsed_ms: float
    error: str | None = None


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[UUID]], *, k: int = 60
) -> list[tuple[UUID, float]]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    scores: dict[UUID, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def weighted_reciprocal_rank_fusion(
    route_results: Sequence[RouteResult],
    *,
    weights: Mapping[str, float],
    k: int = 60,
) -> list[FusedChunk]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    scores: dict[UUID, float] = defaultdict(float)
    traces: dict[UUID, list[RetrievalRouteTrace]] = defaultdict(list)
    for result in route_results:
        weight = weights.get(result.route, 0.0)
        if weight <= 0:
            continue
        for rank, candidate in enumerate(result.candidates, start=1):
            contribution = weight / (k + rank)
            scores[candidate.chunk.id] += contribution
            traces[candidate.chunk.id].append(
                RetrievalRouteTrace(
                    route=result.route,
                    rank=rank,
                    raw_score=candidate.raw_score,
                    weighted_rrf=contribution,
                )
            )
    return [
        FusedChunk(chunk_id=chunk_id, score=score, traces=tuple(traces[chunk_id]))
        for chunk_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]


class SQLiteHybridRetriever:
    """Explainable biomedical retrieval with bounded, independently degradable routes."""

    def __init__(
        self,
        session: AsyncSession,
        token_service: EvidenceTokenService,
        *,
        workflow_id: UUID,
        config: RetrievalConfig,
        embedding_service: SentenceTransformerEmbeddingService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        collection_id: UUID | None = None,
        reranker: CrossEncoderReranker | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory
        self._tokens = token_service
        self._workflow_id = workflow_id
        self._config = config
        self._embedding_service = embedding_service
        self._collection_id = collection_id
        self._reranker = reranker
        self._vector_store = vector_store or get_vector_store(config.vector_backend)
        self.vector_used = False
        self.last_report: RetrievalReport | None = None

    async def retrieve(
        self,
        query: str,
        *,
        project_id: UUID,
        limit: int = 20,
        query_plan: QueryPlan | None = None,
    ) -> list[RetrievalCandidate]:
        started = perf_counter()
        plan = query_plan or DeterministicQueryPlanner().plan(query)
        plan = await self._expand_query_plan_with_project_mesh(plan, project_id)
        route_calls = self._route_calls(plan, project_id, limit)
        if self._session_factory is None:
            route_results = [await call() for call in route_calls]
        else:
            route_results = list(await asyncio.gather(*(call() for call in route_calls)))
        if route_results and all(result.error for result in route_results):
            elapsed_ms = (perf_counter() - started) * 1000
            self.last_report = RetrievalReport(
                version=self._config.version,
                query_language=plan.language,
                original_query=plan.original_query,
                english_query=plan.english_query,
                expansions=tuple((item.source, item.target) for item in plan.expansions),
                routes=tuple(route_results),
                anchors=0,
                evidence_blocks=0,
                elapsed_ms=elapsed_ms,
                reranker=RerankerReport(
                    enabled=self._config.reranker_enabled,
                    applied=False,
                    model=(
                        self._config.reranker_model
                        if self._config.reranker_enabled
                        else None
                    ),
                    candidates=0,
                    elapsed_ms=0.0,
                ),
                excluded_retracted=0,
            )
            logger.warning(
                "retrieval_all_routes_failed version=%s language=%s routes=%s elapsed_ms=%.1f",
                self._config.version,
                plan.language,
                {result.route: result.error for result in route_results},
                elapsed_ms,
            )
            return []
        self.vector_used = any(
            result.candidates and result.route.startswith("dense_")
            for result in route_results
        )
        preliminary_fused = weighted_reciprocal_rank_fusion(
            route_results,
            weights=self._config.weights,
            k=self._config.rrf_k,
        )
        ranked_by_id: dict[UUID, RankedChunk] = {}
        for result in route_results:
            for candidate in result.candidates:
                self._record_ranked(ranked_by_id, candidate)
        preliminary_fused, _ = await self._filter_retracted(
            preliminary_fused,
            ranked_by_id,
            bypass=plan.is_identifier_lookup,
        )

        graph_routes = [
            ("graph_local", "local", self._config.graph_neighbors),
            ("graph_global", "global", self._config.graph_community_top_k),
            ("graph_drift", "drift", self._config.graph_neighbors),
            ("graph_path", "path", self._config.graph_path_top_k),
        ]
        enabled_graph_routes = [
            value
            for value in graph_routes
            if self._config.weights.get(value[0], 0) > 0
            and value[2] > 0
            and self._graph_mode_allowed(value[1], plan)
        ]
        if not plan.is_identifier_lookup and enabled_graph_routes:
            seed_papers = {
                ranked_by_id[item.chunk_id].paper_id
                for item in preliminary_fused[: self._config.graph_seed_papers]
                if item.chunk_id in ranked_by_id
            }
            graph_query = plan.english_query or plan.simple_query or plan.original_query
            for route_name, mode, route_limit in enabled_graph_routes:
                async def graph_operation(
                    session: AsyncSession,
                    selected_mode: str = mode,
                    selected_limit: int = route_limit,
                ) -> RouteCandidateBatch:
                    return await self._graphrag_candidates(
                        session,
                        graph_query,
                        project_id,
                        seed_papers,
                        selected_limit,
                        self._config.graph_hops,
                        selected_mode,
                    )

                graph_result = await self._timed_route(
                    route_name,
                    graph_operation,
                )
                route_results.append(graph_result)
                for candidate in graph_result.candidates:
                    self._record_ranked(ranked_by_id, candidate)

        fused = weighted_reciprocal_rank_fusion(
            route_results,
            weights=self._config.weights,
            k=self._config.rrf_k,
        )
        fused, excluded_retracted = await self._filter_retracted(
            fused,
            ranked_by_id,
            bypass=plan.is_identifier_lookup,
        )
        fused, reranker_report = await self._rerank_fused(
            plan.original_query,
            fused,
            ranked_by_id,
        )
        fused = await self._apply_journal_prior(fused, ranked_by_id)

        anchor_limit = limit if plan.is_identifier_lookup else min(limit, self._config.fused_pool)
        anchors = self._select_diverse_anchors(
            fused,
            ranked_by_id,
            limit=anchor_limit,
            bypass_paper_cap=plan.is_identifier_lookup,
        )
        evidence = await self._expand_context(anchors, plan)
        elapsed_ms = (perf_counter() - started) * 1000
        self.last_report = RetrievalReport(
            version=self._config.version,
            query_language=plan.language,
            original_query=plan.original_query,
            english_query=plan.english_query,
            expansions=tuple((item.source, item.target) for item in plan.expansions),
            routes=tuple(route_results),
            anchors=len(anchors),
            evidence_blocks=len(evidence),
            elapsed_ms=elapsed_ms,
            reranker=reranker_report,
            excluded_retracted=excluded_retracted,
        )
        logger.info(
            "retrieval_completed version=%s language=%s routes=%s anchors=%d blocks=%d "
            "elapsed_ms=%.1f",
            self._config.version,
            plan.language,
            {result.route: len(result.candidates) for result in route_results},
            len(anchors),
            len(evidence),
            elapsed_ms,
        )
        return evidence

    async def _expand_query_plan_with_project_mesh(
        self, plan: QueryPlan, project_id: UUID
    ) -> QueryPlan:
        """Enrich zh/mixed queries with the project's own MeSH terminology.

        Expansion is best-effort and can never fail retrieval: label loading is
        bounded, and database errors fall back to the original plan.
        """
        if not self._config.query_mesh_expansion or plan.language == "en":
            return plan
        try:
            labels = await self._load_project_mesh_labels(project_id)
        except SQLAlchemyError:
            logger.exception("mesh_query_expansion_database_error project=%s", project_id)
            return plan
        if not labels:
            return plan
        expanded = expand_plan_with_mesh(
            plan,
            labels,
            max_terms=self._config.query_mesh_max_terms,
        )
        if expanded.english_query != plan.english_query:
            logger.info(
                "mesh_query_expansion applied=%d language=%s terms=%d",
                len(labels),
                plan.language,
                self._config.query_mesh_max_terms,
            )
        return expanded

    async def _load_project_mesh_labels(self, project_id: UUID) -> list[str]:
        statement = text(
            "SELECT DISTINCT mesh_terms.preferred_label FROM mesh_terms "
            "JOIN paper_mesh ON paper_mesh.descriptor_ui = mesh_terms.descriptor_ui "
            "JOIN project_papers ON project_papers.paper_id = paper_mesh.paper_id "
            "WHERE project_papers.project_id = :project_id "
            "ORDER BY mesh_terms.preferred_label LIMIT 500"
        )
        parameters: dict[str, object] = {"project_id": project_id.hex}
        if self._collection_id is not None:
            statement = text(
                "SELECT DISTINCT mesh_terms.preferred_label FROM mesh_terms "
                "JOIN paper_mesh ON paper_mesh.descriptor_ui = mesh_terms.descriptor_ui "
                "JOIN project_papers ON project_papers.paper_id = paper_mesh.paper_id "
                "JOIN collection_papers ON collection_papers.paper_id = paper_mesh.paper_id "
                "WHERE project_papers.project_id = :project_id "
                "AND collection_papers.collection_id = :collection_id "
                "ORDER BY mesh_terms.preferred_label LIMIT 500"
            )
            parameters["collection_id"] = self._collection_id.hex
        if self._session_factory is None:
            values = await self._session.scalars(statement, parameters)
            return list(values)
        async with self._session_factory() as session:
            values = await session.scalars(statement, parameters)
            return list(values)

    async def _apply_journal_prior(
        self,
        fused: list[FusedChunk],
        ranked_by_id: Mapping[UUID, RankedChunk],
    ) -> list[FusedChunk]:
        weight = self._config.journal_prior_weight
        if weight <= 0 or not fused:
            return fused
        paper_ids = {
            ranked_by_id[item.chunk_id].paper_id
            for item in fused
            if item.chunk_id in ranked_by_id
        }
        if not paper_ids:
            return fused
        rows = (
            await self._session.execute(
                select(Paper.id, JournalMetric.importance_score)
                .outerjoin(JournalMetric, JournalMetric.id == Paper.journal_metric_id)
                .where(Paper.id.in_(paper_ids))
            )
        ).all()
        prior_by_paper = {paper_id: float(score or 0.0) for paper_id, score in rows}
        max_score = max(item.score for item in fused) or 1.0
        values = [
            FusedChunk(
                chunk_id=item.chunk_id,
                score=(1.0 - weight) * item.score
                + weight
                * max_score
                * prior_by_paper.get(ranked_by_id[item.chunk_id].paper_id, 0.0),
                traces=item.traces,
            )
            for item in fused
            if item.chunk_id in ranked_by_id
        ]
        return sorted(values, key=lambda item: item.score, reverse=True)

    @staticmethod
    def _graph_mode_allowed(mode: str, plan: QueryPlan) -> bool:
        if mode == "local":
            return True
        broad = plan.research_question_type in {
            "systematic_review",
            "latest_progress",
        }
        if mode == "global":
            return broad or plan.query_focus in {
                "systematic_reviews",
                "recent_reviews",
                "citation_landscape",
            }
        if mode == "drift":
            return plan.routing_context == "deep_research" and (
                broad
                or plan.query_focus in {"core", "citation_landscape", "open_questions"}
            )
        if mode == "path":
            return plan.research_question_type in {
                "comparison",
                "mechanism",
                "systematic_review",
                "latest_progress",
            } and plan.query_focus in {"core", "citation_landscape", "causal_evidence"}
        return False

    def _route_calls(
        self, plan: QueryPlan, project_id: UUID, limit: int
    ) -> list[Callable[[], Awaitable[RouteResult]]]:
        if plan.is_identifier_lookup:
            return [
                lambda: self._timed_route(
                    "exact",
                    lambda session: self._exact_identifier_candidates(
                        session, plan, project_id, limit
                    ),
                )
            ]
        calls: list[Callable[[], Awaitable[RouteResult]]] = []
        if plan.english_query:
            calls.extend(
                [
                    lambda: self._timed_route(
                        "fts_english",
                        lambda session: self._fts_candidates(
                            session,
                            plan.english_query or "",
                            project_id,
                            self._config.top_k_fts,
                            configuration="english",
                        ),
                    ),
                    lambda: self._timed_route(
                        "metadata",
                        lambda session: self._metadata_candidates(
                            session,
                            plan.english_query or "",
                            project_id,
                            self._config.top_k_metadata,
                        ),
                    ),
                    lambda: self._timed_route(
                        "mesh",
                        lambda session: self._mesh_candidates(
                            session,
                            plan.english_query or "",
                            project_id,
                            self._config.top_k_metadata,
                        ),
                    ),
                ]
            )
        if plan.simple_query:
            calls.append(
                lambda: self._timed_route(
                    "simple",
                    lambda session: self._fts_candidates(
                        session,
                        plan.simple_query or "",
                        project_id,
                        self._config.top_k_simple,
                        configuration="simple",
                    ),
                )
            )
        for route, dense_query in plan.dense_queries:
            calls.append(self._dense_route_call(route, dense_query, project_id))
        return calls

    def _dense_route_call(
        self, route: str, dense_query: str, project_id: UUID
    ) -> Callable[[], Awaitable[RouteResult]]:
        async def call() -> RouteResult:
            return await self._timed_route(
                route,
                lambda session: self._vector_candidates(
                    session,
                    dense_query,
                    project_id,
                    self._config.top_k_dense,
                ),
            )

        return call

    async def _timed_route(
        self,
        route: str,
        operation: Callable[
            [AsyncSession],
            Awaitable[list[RankedChunk] | RouteCandidateBatch],
        ],
    ) -> RouteResult:
        started = perf_counter()
        try:
            async def execute() -> list[RankedChunk] | RouteCandidateBatch:
                if self._session_factory is None:
                    return await operation(self._session)
                async with self._session_factory() as route_session:
                    return await operation(route_session)

            timeout = (
                self._config.dense_route_timeout_seconds
                if route.startswith("dense_")
                else self._config.route_timeout_seconds
            )
            value = await asyncio.wait_for(execute(), timeout=timeout)
            if isinstance(value, RouteCandidateBatch):
                candidates = value.candidates
                metrics = value.metrics
            else:
                candidates = tuple(value)
                metrics = {}
            return RouteResult(
                route,
                candidates,
                (perf_counter() - started) * 1000,
                metrics=metrics,
            )
        except TimeoutError:
            return RouteResult(
                route,
                (),
                (perf_counter() - started) * 1000,
                "route_timeout",
            )
        except EmbeddingUnavailableError:
            return RouteResult(
                route,
                (),
                (perf_counter() - started) * 1000,
                "embedding_unavailable",
            )
        except VectorStoreError:
            logger.exception("retrieval_vector_store_error route=%s", route)
            return RouteResult(
                route,
                (),
                (perf_counter() - started) * 1000,
                "vector_store_error",
            )
        except SQLAlchemyError:
            logger.exception("retrieval_route_database_error route=%s", route)
            return RouteResult(
                route,
                (),
                (perf_counter() - started) * 1000,
                "database_error",
            )

    async def _exact_identifier_candidates(
        self,
        session: AsyncSession,
        plan: QueryPlan,
        project_id: UUID,
        limit: int,
    ) -> list[RankedChunk]:
        value = plan.identifier_value or plan.original_query
        condition = {
            "pmid": Paper.pmid == value,
            "pmcid": Paper.pmcid == value,
            "doi": Paper.doi_normalized == value,
        }.get(plan.identifier_kind or "", Paper.id.is_(None))
        statement = (
            select(Chunk, Paper.id)
            .join(Section, Chunk.section_id == Section.id)
            .join(DocumentAsset, Section.asset_id == DocumentAsset.id)
            .join(Paper, DocumentAsset.paper_id == Paper.id)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(ProjectPaper.project_id == project_id, condition)
            .order_by(Section.ordinal, Chunk.ordinal)
            .limit(limit)
        )
        if self._collection_id:
            statement = statement.where(
                Paper.id.in_(
                    select(CollectionPaper.paper_id).where(
                        CollectionPaper.collection_id == self._collection_id
                    )
                )
            )
        return [
            RankedChunk(chunk=chunk, paper_id=paper_id, raw_score=1.0)
            for chunk, paper_id in (await session.execute(statement)).all()
        ]

    async def _fts_candidates(
        self,
        session: AsyncSession,
        query: str,
        project_id: UUID,
        limit: int,
        *,
        configuration: str,
    ) -> list[RankedChunk]:
        table = "chunk_fts_english" if configuration == "english" else "chunk_fts_simple"
        collection_join = ""
        collection_filter = ""
        parameters: dict[str, object] = {
            "query": self._sqlite_fts_query(query),
            "project_id": project_id.hex,
            "limit": limit,
        }
        if self._collection_id:
            collection_join = "JOIN collection_papers cp ON cp.paper_id = da.paper_id "
            collection_filter = "AND cp.collection_id = :collection_id "
            parameters["collection_id"] = self._collection_id.hex
        statement = text(
            f"SELECT c.id, -bm25({table}) AS score "
            f"FROM {table} "
            f"JOIN chunks c ON CAST(c.id AS TEXT) = {table}.chunk_id "
            "JOIN sections s ON s.id = c.section_id "
            "JOIN document_assets da ON da.id = s.asset_id "
            "JOIN project_papers pp ON pp.paper_id = da.paper_id "
            f"{collection_join}"
            f"WHERE {table} MATCH :query AND pp.project_id = :project_id "
            f"{collection_filter}"
            f"ORDER BY bm25({table}) LIMIT :limit"
        )
        rows = (
            await session.execute(statement, parameters)
        ).all()
        return await self._load_ranked_rows(session, rows)

    async def _metadata_candidates(
        self, session: AsyncSession, query: str, project_id: UUID, limit: int
    ) -> list[RankedChunk]:
        collection_join = ""
        collection_filter = ""
        parameters: dict[str, object] = {
            "query": self._sqlite_fts_query(query),
            "project_id": project_id.hex,
            "limit": limit,
        }
        if self._collection_id:
            collection_join = "JOIN collection_papers cp ON cp.paper_id = p.id "
            collection_filter = "AND cp.collection_id = :collection_id "
            parameters["collection_id"] = self._collection_id.hex
        statement = text(
            "SELECT c.id, -bm25(paper_fts) AS score "
            "FROM paper_fts "
            "JOIN papers p ON CAST(p.id AS TEXT) = paper_fts.paper_id "
            "JOIN project_papers pp ON pp.paper_id = p.id "
            f"{collection_join}"
            "JOIN document_assets da ON da.paper_id = p.id "
            "JOIN sections s ON s.asset_id = da.id "
            "JOIN chunks c ON c.section_id = s.id "
            "WHERE paper_fts MATCH :query AND pp.project_id = :project_id "
            f"{collection_filter}"
            "ORDER BY bm25(paper_fts), s.ordinal, c.ordinal LIMIT :limit"
        )
        rows = (
            await session.execute(statement, parameters)
        ).all()
        return await self._load_ranked_rows(session, rows)

    async def _mesh_candidates(
        self, session: AsyncSession, query: str, project_id: UUID, limit: int
    ) -> list[RankedChunk]:
        collection_join = ""
        collection_filter = ""
        parameters: dict[str, object] = {
            "query": self._sqlite_fts_query(query),
            "project_id": project_id.hex,
            "limit": limit,
        }
        if self._collection_id:
            collection_join = "JOIN collection_papers cp ON cp.paper_id = pm.paper_id "
            collection_filter = "AND cp.collection_id = :collection_id "
            parameters["collection_id"] = self._collection_id.hex
        statement = text(
            "SELECT c.id, (-bm25(mesh_fts) + CASE WHEN pm.is_major_topic THEN 0.1 ELSE 0 END) "
            "AS score FROM mesh_fts "
            "JOIN paper_mesh pm ON pm.descriptor_ui = mesh_fts.descriptor_ui "
            "JOIN project_papers pp ON pp.paper_id = pm.paper_id "
            f"{collection_join}"
            "JOIN document_assets da ON da.paper_id = pm.paper_id "
            "JOIN sections s ON s.asset_id = da.id "
            "JOIN chunks c ON c.section_id = s.id "
            "WHERE mesh_fts MATCH :query AND pp.project_id = :project_id "
            f"{collection_filter}"
            "ORDER BY score DESC, s.ordinal, c.ordinal LIMIT :limit"
        )
        rows = (
            await session.execute(statement, parameters)
        ).all()
        return self._deduplicate_ranked(await self._load_ranked_rows(session, rows))

    async def _vector_candidates(
        self, session: AsyncSession, query: str, project_id: UUID, limit: int
    ) -> RouteCandidateBatch:
        service = self._embedding_service
        if service is None:
            return RouteCandidateBatch(())
        candidate_count = await self._vector_store.candidate_count(
            session,
            project_id=project_id,
            collection_id=self._collection_id,
            model_name=service.model_name,
            dimension=service.dimension,
        )
        if candidate_count == 0:
            return RouteCandidateBatch(
                (),
                {
                    "backend": self._vector_store.backend_name,
                    "candidate_count": 0,
                    "vector_bytes": 0,
                    "fetch_ms": 0.0,
                    "decode_ms": 0.0,
                    "dot_product_ms": 0.0,
                    "top_k_ms": 0.0,
                    "estimated_working_set_bytes": 0,
                    "embedding_skipped": 1,
                },
            )
        query_vectors = await service.embed_queries([query])
        if not query_vectors:
            return RouteCandidateBatch(())
        result = await self._vector_store.search(
            session,
            query_vector=query_vectors[0],
            project_id=project_id,
            collection_id=self._collection_id,
            model_name=service.model_name,
            dimension=service.dimension,
            limit=limit,
        )
        if not result.matches:
            return RouteCandidateBatch((), result.metrics.as_dict())
        chunk_ids = [match.chunk_id for match in result.matches]
        chunks = {
            chunk.id: chunk
            for chunk in (
                await session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))
            ).all()
        }
        candidates = tuple(
            RankedChunk(
                chunk=chunks[match.chunk_id],
                paper_id=match.paper_id,
                raw_score=match.score,
            )
            for match in result.matches
            if match.chunk_id in chunks
        )
        return RouteCandidateBatch(candidates, result.metrics.as_dict())

    async def _graph_candidates(
        self,
        session: AsyncSession,
        query: str,
        project_id: UUID,
        seed_paper_ids: set[UUID],
        limit: int,
        hops: int,
    ) -> list[RankedChunk]:
        neighbor_ids = await citation_neighbor_papers(
            session,
            project_id=project_id,
            seed_paper_ids=seed_paper_ids,
            limit=limit,
            scope_key=(
                f"collection:{self._collection_id}" if self._collection_id else "project"
            ),
            hops=hops,
        )
        if not neighbor_ids:
            return []
        statement = (
            select(Chunk, DocumentAsset.paper_id, Paper.title)
            .join(Section, Chunk.section_id == Section.id)
            .join(DocumentAsset, Section.asset_id == DocumentAsset.id)
            .join(Paper, Paper.id == DocumentAsset.paper_id)
            .where(DocumentAsset.paper_id.in_(neighbor_ids))
        )
        terms = {term.casefold() for term in query.split() if len(term) > 1}
        candidates: list[RankedChunk] = []
        for chunk, paper_id, title in (await session.execute(statement)).all():
            searchable = f"{title} {chunk.text}".casefold()
            matched = sum(1 for term in terms if term in searchable)
            if matched:
                candidates.append(
                    RankedChunk(
                        chunk=chunk,
                        paper_id=paper_id,
                        raw_score=matched / max(len(terms), 1),
                    )
                )
        return sorted(candidates, key=lambda value: value.raw_score, reverse=True)[:limit]

    async def _graphrag_candidates(
        self,
        session: AsyncSession,
        query: str,
        project_id: UUID,
        seed_paper_ids: set[UUID],
        limit: int,
        hops: int,
        mode: str,
    ) -> RouteCandidateBatch:
        scope_key = (
            f"collection:{self._collection_id}" if self._collection_id else "project"
        )
        result = await FullGraphRagRetriever(session).search(
            mode=mode,
            query=query,
            project_id=project_id,
            scope_key=scope_key,
            seed_paper_ids=seed_paper_ids,
            limit=limit,
            hops=hops,
            persist_paths=False,
        )
        if not result.hits:
            return RouteCandidateBatch((), result.metrics)
        chunk_ids = [hit.chunk_id for hit in result.hits]
        chunks = {
            chunk.id: chunk
            for chunk in (
                await session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))
            ).all()
        }
        return RouteCandidateBatch(
            tuple(
                RankedChunk(
                    chunk=chunks[hit.chunk_id],
                    paper_id=hit.paper_id,
                    raw_score=hit.score,
                    provenance={"graph_routes": [dict(hit.provenance)]},
                )
                for hit in result.hits
                if hit.chunk_id in chunks
            ),
            result.metrics,
        )

    @staticmethod
    def _sqlite_fts_query(query: str) -> str:
        terms = [
            term.replace('"', "")
            for term in query.split()
            if term.replace('"', "").strip()
        ]
        if not terms:
            return '"__no_match__"'
        return " OR ".join(f'"{term}"' for term in terms)

    async def _load_ranked_rows(
        self, session: AsyncSession, rows: Sequence[Row[Any]]
    ) -> list[RankedChunk]:
        normalized_rows: list[tuple[Any, ...]] = [tuple(row) for row in rows]
        chunk_ids = [UUID(str(row[0])) for row in normalized_rows]
        if not chunk_ids:
            return []
        statement = (
            select(Chunk, DocumentAsset.paper_id)
            .join(Section, Chunk.section_id == Section.id)
            .join(DocumentAsset, Section.asset_id == DocumentAsset.id)
            .where(Chunk.id.in_(chunk_ids))
        )
        loaded = {
            chunk.id: (chunk, paper_id)
            for chunk, paper_id in (await session.execute(statement)).all()
        }
        return [
            RankedChunk(
                chunk=loaded[chunk_id][0],
                paper_id=loaded[chunk_id][1],
                raw_score=float(row[1]),
            )
            for chunk_id, row in zip(chunk_ids, normalized_rows, strict=True)
            if chunk_id in loaded
        ]

    @staticmethod
    def _deduplicate_ranked(values: Sequence[RankedChunk]) -> list[RankedChunk]:
        seen: set[UUID] = set()
        result: list[RankedChunk] = []
        for value in values:
            if value.chunk.id in seen:
                continue
            seen.add(value.chunk.id)
            result.append(value)
        return result

    @staticmethod
    def _record_ranked(
        values: dict[UUID, RankedChunk],
        candidate: RankedChunk,
    ) -> None:
        current = values.get(candidate.chunk.id)
        if current is None:
            values[candidate.chunk.id] = candidate
            return
        if candidate.provenance:
            current_routes = current.provenance.get("graph_routes", [])
            candidate_routes = candidate.provenance.get("graph_routes", [])
            merged_routes = [
                *(
                    list(current_routes)
                    if isinstance(current_routes, list)
                    else []
                ),
                *(
                    list(candidate_routes)
                    if isinstance(candidate_routes, list)
                    else []
                ),
            ]
            values[candidate.chunk.id] = RankedChunk(
                chunk=current.chunk,
                paper_id=current.paper_id,
                raw_score=current.raw_score,
                provenance={
                    **current.provenance,
                    **candidate.provenance,
                    "graph_routes": merged_routes,
                },
            )

    def _select_diverse_anchors(
        self,
        fused: Sequence[FusedChunk],
        ranked_by_id: Mapping[UUID, RankedChunk],
        *,
        limit: int,
        bypass_paper_cap: bool,
    ) -> list[tuple[FusedChunk, RankedChunk]]:
        seen_hashes: set[str] = set()
        paper_counts: dict[UUID, int] = defaultdict(int)
        selected: list[tuple[FusedChunk, RankedChunk]] = []
        for item in fused[: self._config.fused_pool]:
            ranked = ranked_by_id.get(item.chunk_id)
            if ranked is None or ranked.chunk.content_hash in seen_hashes:
                continue
            if (
                not bypass_paper_cap
                and paper_counts[ranked.paper_id] >= self._config.max_chunks_per_paper
            ):
                continue
            seen_hashes.add(ranked.chunk.content_hash)
            paper_counts[ranked.paper_id] += 1
            selected.append((item, ranked))
            if len(selected) >= limit:
                break
        return selected

    async def _rerank_fused(
        self,
        query: str,
        fused: list[FusedChunk],
        ranked_by_id: Mapping[UUID, RankedChunk],
    ) -> tuple[list[FusedChunk], RerankerReport]:
        enabled = self._config.reranker_enabled
        model = self._config.reranker_model if enabled else None
        if not enabled or self._reranker is None or not fused:
            return fused, RerankerReport(enabled, False, model, 0, 0.0, None)
        candidates = [
            item
            for item in fused[: self._config.reranker_candidates]
            if item.chunk_id in ranked_by_id
        ]
        documents = [
            RerankDocument(item.chunk_id, ranked_by_id[item.chunk_id].chunk.text)
            for item in candidates
        ]
        if not documents:
            return fused, RerankerReport(enabled, False, model, 0, 0.0, None)
        started = perf_counter()
        try:
            raw_scores = await asyncio.wait_for(
                self._reranker.score(query, documents),
                timeout=self._config.reranker_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "reranker_timeout model=%s timeout=%.1fs",
                model,
                self._config.reranker_timeout_seconds,
            )
            return fused, RerankerReport(
                enabled,
                False,
                model,
                len(documents),
                (perf_counter() - started) * 1000,
                "reranker_timeout",
            )
        except RerankerUnavailableError as exc:
            logger.warning("reranker_unavailable model=%s reason=%s", model, exc)
            return fused, RerankerReport(
                enabled,
                False,
                model,
                len(documents),
                (perf_counter() - started) * 1000,
                str(exc),
            )
        rerank_values = list(raw_scores.values())
        rerank_min = min(rerank_values)
        rerank_span = max(rerank_values) - rerank_min
        rrf_values = [item.score for item in candidates]
        rrf_min = min(rrf_values)
        rrf_span = max(rrf_values) - rrf_min
        weight = self._config.reranker_weight
        rescored: dict[UUID, FusedChunk] = {}
        for item in candidates:
            normalized_rerank = (
                (raw_scores[item.chunk_id] - rerank_min) / rerank_span
                if rerank_span > 0
                else 1.0
            )
            normalized_rrf = (
                (item.score - rrf_min) / rrf_span if rrf_span > 0 else 1.0
            )
            score = (1 - weight) * normalized_rrf + weight * normalized_rerank
            rescored[item.chunk_id] = FusedChunk(
                chunk_id=item.chunk_id,
                score=score,
                traces=(
                    *item.traces,
                    RetrievalRouteTrace(
                        route="reranker",
                        rank=0,
                        raw_score=raw_scores[item.chunk_id],
                        weighted_rrf=0.0,
                    ),
                ),
            )
        reranked_prefix = sorted(
            (rescored[item.chunk_id] for item in candidates),
            key=lambda item: item.score,
            reverse=True,
        )
        candidate_ids = {item.chunk_id for item in candidates}
        output = [
            *reranked_prefix,
            *[item for item in fused if item.chunk_id not in candidate_ids],
        ]
        return output, RerankerReport(
            enabled,
            True,
            model,
            len(documents),
            (perf_counter() - started) * 1000,
            None,
        )

    async def _filter_retracted(
        self,
        fused: list[FusedChunk],
        ranked_by_id: Mapping[UUID, RankedChunk],
        *,
        bypass: bool,
    ) -> tuple[list[FusedChunk], int]:
        if bypass or not self._config.exclude_retracted or not fused:
            return fused, 0
        paper_ids = {
            ranked_by_id[item.chunk_id].paper_id
            for item in fused
            if item.chunk_id in ranked_by_id
        }
        if not paper_ids:
            return fused, 0
        async def execute(session: AsyncSession) -> set[UUID]:
            return set(
                (
                    await session.scalars(
                        select(Paper.id).where(
                            Paper.id.in_(paper_ids),
                            Paper.is_retracted.is_(True),
                        )
                    )
                ).all()
            )

        if self._session_factory is None:
            retracted = await execute(self._session)
        else:
            async with self._session_factory() as filter_session:
                retracted = await execute(filter_session)
        if not retracted:
            return fused, 0
        output = [
            item
            for item in fused
            if item.chunk_id not in ranked_by_id
            or ranked_by_id[item.chunk_id].paper_id not in retracted
        ]
        return output, len(fused) - len(output)

    async def _expand_context(
        self,
        anchors: Sequence[tuple[FusedChunk, RankedChunk]],
        plan: QueryPlan,
    ) -> list[RetrievalCandidate]:
        groups: dict[UUID, list[RankedChunk]] = {
            ranked.chunk.id: [] for _, ranked in anchors
        }
        previous_frontier = {
            ranked.chunk.id: ranked.chunk.previous_chunk_id for _, ranked in anchors
        }
        next_frontier = {
            ranked.chunk.id: ranked.chunk.next_chunk_id for _, ranked in anchors
        }
        for _ in range(self._config.context_radius):
            requested = {
                value
                for value in [*previous_frontier.values(), *next_frontier.values()]
                if value is not None
            }
            loaded = await self._load_chunks(requested)
            for _, ranked in anchors:
                anchor_id = ranked.chunk.id
                previous_id = previous_frontier.get(anchor_id)
                if previous_id and previous_id in loaded:
                    previous = loaded[previous_id]
                    groups[anchor_id].insert(0, previous)
                    previous_frontier[anchor_id] = previous.chunk.previous_chunk_id
                else:
                    previous_frontier[anchor_id] = None
                next_id = next_frontier.get(anchor_id)
                if next_id and next_id in loaded:
                    following = loaded[next_id]
                    groups[anchor_id].append(following)
                    next_frontier[anchor_id] = following.chunk.next_chunk_id
                else:
                    next_frontier[anchor_id] = None

        output: list[RetrievalCandidate] = []
        included: set[UUID] = set()
        used_characters = 0
        for fused, ranked in anchors:
            anchor = ranked.chunk
            previous_context = [
                item for item in groups[anchor.id] if item.chunk.ordinal < anchor.ordinal
            ]
            following_context = [
                item for item in groups[anchor.id] if item.chunk.ordinal > anchor.ordinal
            ]
            contextual_neighbors = self._query_aware_neighbors(
                [*previous_context, *following_context],
                anchor=anchor,
                plan=plan,
            )
            previous_context = [
                item for item in contextual_neighbors if item.chunk.ordinal < anchor.ordinal
            ]
            following_context = [
                item for item in contextual_neighbors if item.chunk.ordinal > anchor.ordinal
            ]
            for value in [*previous_context, ranked, *following_context]:
                if value.chunk.id in included:
                    continue
                if (
                    output
                    and used_characters + len(value.chunk.text)
                    > self._config.context_max_chars
                ):
                    continue
                is_anchor = value.chunk.id == anchor.id
                traces = fused.traces if is_anchor else (
                    RetrievalRouteTrace("neighbor", 0, 0.0, 0.0),
                )
                locator = dict(value.chunk.source_locator)
                locator.update(
                    {
                        "paper_id": str(value.paper_id),
                        "retrieval_role": "anchor" if is_anchor else "neighbor",
                        "anchor_chunk_id": str(anchor.id),
                        "retrieval_routes": [trace.route for trace in traces],
                        "retrieval_version": self._config.version,
                    }
                )
                if is_anchor and ranked.provenance:
                    locator["graph_provenance"] = dict(ranked.provenance)
                output.append(
                    RetrievalCandidate(
                        chunk_id=value.chunk.id,
                        evidence_id=self._tokens.issue(self._workflow_id, value.chunk.id),
                        text=value.chunk.text,
                        score=fused.score,
                        source_locator=locator,
                        paper_id=value.paper_id,
                        content_hash=value.chunk.content_hash,
                        traces=traces,
                        role="anchor" if is_anchor else "neighbor",
                        anchor_chunk_id=anchor.id,
                    )
                )
                included.add(value.chunk.id)
                used_characters += len(value.chunk.text)
        return output

    def _query_aware_neighbors(
        self,
        values: list[RankedChunk],
        *,
        anchor: Chunk,
        plan: QueryPlan,
    ) -> list[RankedChunk]:
        if not self._config.neighbor_query_filter:
            return values
        limit = self._config.neighbor_max_per_anchor
        if limit <= 0:
            return []
        query = " ".join(
            value
            for value in (
                plan.original_query,
                plan.english_query or "",
                plan.simple_query or "",
            )
            if value
        )
        terms = self._context_terms(query)
        if not terms:
            return values[:limit]
        scored: list[tuple[float, int, RankedChunk]] = []
        for value in values:
            searchable = (
                f"{value.chunk.source_locator.get('section_path', '')} {value.chunk.text}"
            )
            candidate_terms = self._context_terms(searchable)
            overlap = len(terms & candidate_terms) / max(len(terms), 1)
            distance = abs(value.chunk.ordinal - anchor.ordinal)
            score = overlap + (0.03 / max(distance, 1))
            if score >= self._config.neighbor_min_score:
                scored.append((score, -distance, value))
        selected = [
            item[2]
            for item in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[
                :limit
            ]
        ]
        return sorted(selected, key=lambda item: item.chunk.ordinal)

    @staticmethod
    def _context_terms(value: str) -> set[str]:
        output: set[str] = set()
        for token in re.findall(
            r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]+",
            value.casefold(),
        ):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                output.update(
                    token[index : index + 2] for index in range(len(token) - 1)
                )
            else:
                output.add(token)
        return output

    async def _load_chunks(self, chunk_ids: set[UUID]) -> dict[UUID, RankedChunk]:
        if not chunk_ids:
            return {}

        async def execute(session: AsyncSession) -> dict[UUID, RankedChunk]:
            statement = (
                select(Chunk, DocumentAsset.paper_id)
                .join(Section, Chunk.section_id == Section.id)
                .join(DocumentAsset, Section.asset_id == DocumentAsset.id)
                .where(Chunk.id.in_(chunk_ids))
            )
            return {
                chunk.id: RankedChunk(chunk=chunk, paper_id=paper_id, raw_score=0.0)
                for chunk, paper_id in (await session.execute(statement)).all()
            }

        if self._session_factory is None:
            return await execute(self._session)
        async with self._session_factory() as route_session:
            return await execute(route_session)
