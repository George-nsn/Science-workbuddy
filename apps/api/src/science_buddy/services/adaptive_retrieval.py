import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Literal
from uuid import UUID

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.services.cache import ThreeLevelCache
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.query_planning import DeterministicQueryPlanner, QueryPlan
from science_buddy.services.research_routing import (
    ControlledResearchRetriever,
    ControlledRetrievalResult,
    ControlledRetrievalStep,
    ResearchRoutingDecision,
)
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever
from science_buddy.services.retrieval_cache import RetrievalExecution, retrieve_with_cache


def routed_query_plan(
    query: str,
    *,
    decision: ResearchRoutingDecision,
    subquery_id: str,
    focus: str,
    parent_query: str,
) -> QueryPlan:
    base_plan = DeterministicQueryPlanner().plan(query)
    is_primary = subquery_id == "q1"
    explicit_focus = {
        "graph_local_search": (decision.question_type, "core", decision.strategy),
        "graph_global_search": ("systematic_review", "systematic_reviews", "deep_research"),
        "graph_drift_search": ("latest_progress", "open_questions", "deep_research"),
        "graph_path_search": ("mechanism", "causal_evidence", "deep_research"),
        "citation_landscape": ("systematic_review", "citation_landscape", "deep_research"),
    }.get(focus)
    question_type = explicit_focus[0] if explicit_focus else decision.question_type
    query_focus = explicit_focus[1] if explicit_focus else focus
    routing_context = explicit_focus[2] if explicit_focus else decision.strategy
    return replace(
        base_plan,
        english_query=(
            base_plan.english_query
            if decision.strategy == "direct" or is_primary
            else None
        ),
        dense_queries=(
            base_plan.dense_queries
            if decision.strategy == "direct" or is_primary
            else ()
        ),
        expansions=(
            base_plan.expansions
            if decision.strategy == "direct" or is_primary
            else ()
        ),
        routing_context=routing_context,
        research_question_type=question_type,
        query_focus=query_focus,
        parent_query=parent_query,
        subquery_id=subquery_id,
    )


class AdaptiveRetrievalService:
    """Bind bounded research routing to the existing scoped, cached retriever."""

    def __init__(
        self,
        *,
        cache: ThreeLevelCache,
        token_service: EvidenceTokenService,
        workflow_id: UUID,
        project_id: UUID,
        collection_id: UUID | None,
        project_revision: int,
        config: RetrievalConfig,
        retriever_factory: Callable[[], SQLiteHybridRetriever],
        retrieve_cached: Callable[..., Awaitable[RetrievalExecution]] = retrieve_with_cache,
    ) -> None:
        self._cache = cache
        self._tokens = token_service
        self._workflow_id = workflow_id
        self._project_id = project_id
        self._collection_id = collection_id
        self._project_revision = project_revision
        self._config = config
        self._retriever_factory = retriever_factory
        self._retrieve_cached = retrieve_cached

    async def execute(
        self,
        decision: ResearchRoutingDecision,
        *,
        limit: int,
        followup_queries: tuple[str, ...] = (),
        include_initial: bool = True,
    ) -> ControlledRetrievalResult:
        parent_query = decision.subqueries[0].query if decision.subqueries else ""

        async def retrieve(
            query: str,
            subquery_id: str,
            focus: str,
            round_number: int,
        ) -> ControlledRetrievalStep:
            retriever: SQLiteHybridRetriever = self._retriever_factory()
            query_plan = routed_query_plan(
                query,
                decision=decision,
                subquery_id=subquery_id,
                focus=focus,
                parent_query=parent_query,
            )
            execution = await self._retrieve_cached(
                retriever=retriever,
                cache=self._cache,
                token_service=self._tokens,
                query_plan=query_plan,
                workflow_id=self._workflow_id,
                project_id=self._project_id,
                collection_id=self._collection_id,
                project_revision=self._project_revision,
                limit=limit,
                config=self._config,
            )
            route_errors = tuple(
                f"{route['route']}:{route['error']}"
                for route in execution.report.get("routes", [])
                if route.get("error")
            )
            return ControlledRetrievalStep(
                subquery_id=subquery_id,
                focus=focus,
                query=query,
                round=round_number,
                candidates=tuple(execution.candidates),
                cache_level=execution.cache_level,
                route_errors=route_errors,
                report=execution.report,
            )

        return await ControlledResearchRetriever(
            retrieve,
            max_parallel=self._config.subquery_parallelism,
        ).execute(
            decision,
            limit=limit,
            followup_queries=followup_queries,
            include_initial=include_initial,
        )

    async def execute_actions(
        self,
        decision: ResearchRoutingDecision,
        *,
        actions: tuple[tuple[str, str, str], ...],
        limit: int,
        round_number: int,
    ) -> ControlledRetrievalResult:
        """Execute approved (action_id, action_type, query) tuples through scoped RAG."""

        parent_query = decision.subqueries[0].query if decision.subqueries else ""
        semaphore = asyncio.Semaphore(self._config.subquery_parallelism)

        async def retrieve_action(
            action_id: str,
            action_type: str,
            query: str,
        ) -> ControlledRetrievalStep:
            async with semaphore:
                retriever = self._retriever_factory()
                query_plan = routed_query_plan(
                    query,
                    decision=decision,
                    subquery_id=action_id,
                    focus=action_type,
                    parent_query=parent_query,
                )
                execution = await self._retrieve_cached(
                    retriever=retriever,
                    cache=self._cache,
                    token_service=self._tokens,
                    query_plan=query_plan,
                    workflow_id=self._workflow_id,
                    project_id=self._project_id,
                    collection_id=self._collection_id,
                    project_revision=self._project_revision,
                    limit=limit,
                    config=self._config,
                )
                route_errors = tuple(
                    f"{route['route']}:{route['error']}"
                    for route in execution.report.get("routes", [])
                    if route.get("error")
                )
                return ControlledRetrievalStep(
                    subquery_id=action_id,
                    focus=action_type,
                    query=query,
                    round=round_number,
                    candidates=tuple(execution.candidates),
                    cache_level=execution.cache_level,
                    route_errors=route_errors,
                    report=execution.report,
                )

        steps = tuple(
            await asyncio.gather(
                *(
                    retrieve_action(action_id, action_type, query)
                    for action_id, action_type, query in actions
                )
            )
        )
        return ControlledRetrievalResult(
            decision=decision,
            candidates=tuple(
                ControlledResearchRetriever.merge_candidates(steps, limit=limit)
            ),
            steps=steps,
            followup_queries=tuple(query for _, _, query in actions),
        )


def retrieval_mode(
    candidates: list[RetrievalCandidate] | tuple[RetrievalCandidate, ...],
    *,
    identifier: bool = False,
) -> Literal["identifier", "hybrid-sparse", "hybrid-dense"]:
    if identifier:
        return "identifier"
    has_dense = any(
        trace.route.startswith("dense_")
        for candidate in candidates
        for trace in candidate.traces
    )
    return "hybrid-dense" if has_dense else "hybrid-sparse"
