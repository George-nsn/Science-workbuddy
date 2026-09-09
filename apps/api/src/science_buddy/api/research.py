import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    ClaimEvidenceMatrixRowResponse,
    ModelStatusResponse,
    ResearchActionResponse,
    ResearchAnswerRequest,
    ResearchAnswerResponse,
    ResearchClaimTargetResponse,
    ResearchFactsPublishRequest,
    ResearchFactsPublishResponse,
    ResearchPlanResponse,
    ResearchPlanVersionResponse,
    ResearchProgressStepResponse,
    ResearchReviewResponse,
    ResearchRoundTraceResponse,
    ResearchRoutingDecisionResponse,
    ResearchToolActionResponse,
    ResearchTraceResponse,
    RetrievalStepAuditResponse,
    SufficiencyVerdictResponse,
)
from science_buddy.config import Settings, get_settings
from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.providers import LiteratureProvider
from science_buddy.infrastructure.database import async_session_factory, get_session
from science_buddy.infrastructure.models import Paper, Project, RagCollection, ResearchRun
from science_buddy.services.adaptive_retrieval import AdaptiveRetrievalService
from science_buddy.services.adaptive_retrieval import retrieval_mode as derive_retrieval_mode
from science_buddy.services.citations import citation_map_for_candidates
from science_buddy.services.controlled_research import ControlledDynamicResearchService
from science_buddy.services.dynamic_research import (
    ActionDecision,
    DynamicResearchPlanner,
    ResearchBudget,
    ResearchPermissions,
    ResearchPlan,
    SufficiencyVerdict,
)
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.evidence import EvidenceTokenService, MechanicalEvidenceVerifier
from science_buddy.services.exports import (
    research_result_bibtex,
    research_result_docx,
    research_result_markdown,
    research_result_ris,
)
from science_buddy.services.literature.common import LiteratureProviderError, normalize_doi
from science_buddy.services.memory_context import MemoryContextService
from science_buddy.services.models import (
    ModelConfigurationError,
    ModelResponseError,
    build_model_provider,
    drain_model_usage,
    model_settings_configured,
    resolve_model_settings,
)
from science_buddy.services.reranking import get_reranker_service
from science_buddy.services.research import ResearchPipeline, ResearchResult
from science_buddy.services.research_audit import ResearchAuditService, ResearchAuditTrail
from science_buddy.services.research_facts import ResearchFactPublisher
from science_buddy.services.research_routing import (
    DeterministicResearchRouter,
    retrieval_steps_payload,
    routing_audit_payload,
)
from science_buddy.services.research_trace import (
    build_research_trace,
    trace_audit_package,
    traceable_review_markdown,
)
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever
from science_buddy.services.retrieval_cache import (
    retrieval_config_payload,
    retrieve_with_cache,
    retrieve_without_cache,
)
from science_buddy.services.route_memory import RetrievalRouteMemoryService
from science_buddy.services.usage import UsageService
from science_buddy.services.web_search import (
    TavilySearchService,
    WebSearchError,
    resolve_web_search_settings,
)

router = APIRouter(prefix="/research", tags=["research"])
logger = logging.getLogger(__name__)
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


async def _supplement_scholarly(
    session: AsyncSession,
    settings: Settings,
    *,
    project_id: UUID,
    queries: tuple[str, ...],
) -> int:
    if not queries:
        return 0
    async with httpx.AsyncClient(
        timeout=settings.literature_request_timeout_seconds,
        follow_redirects=True,
    ) as client:
        from science_buddy.services.literature import (
            CrossrefProvider,
            EuropePmcProvider,
            OpenAlexProvider,
            PubMedProvider,
        )
        from science_buddy.services.literature.discovery import group_literature_records

        providers: list[LiteratureProvider] = [
            PubMedProvider(
                client,
                base_url=settings.ncbi_base_url,
                tool=settings.ncbi_tool,
                email=settings.ncbi_email,
                api_key=(
                    settings.ncbi_api_key.get_secret_value()
                    if settings.ncbi_api_key
                    else None
                ),
            ),
            EuropePmcProvider(client, base_url=settings.europe_pmc_base_url),
            OpenAlexProvider(
                client,
                base_url=settings.openalex_base_url,
                api_key=(
                    settings.openalex_api_key.get_secret_value()
                    if settings.openalex_api_key
                    else None
                ),
            ),
            CrossrefProvider(client, base_url=settings.crossref_base_url),
        ]
        values = await asyncio.gather(
            *(
                provider.search(query[:500], limit=10)
                for query in queries[: settings.research_literature_queries]
                for provider in providers
            ),
            return_exceptions=True,
        )
        records = [
            record
            for value in values
            if isinstance(value, list)
            for record in value
        ]
    from science_buddy.services.literature.ingestion import LiteratureIngestionService

    ingestor = LiteratureIngestionService(
        session,
        default_project_name=settings.default_project_name,
    )
    paper_ids: set[UUID] = set()
    for _, grouped in group_literature_records(records)[: settings.research_literature_target]:
        for record in grouped:
            paper_ids.add((await ingestor.ingest(record, project_id=project_id)).paper_id)
    await session.flush()
    return len(paper_ids)


def _research_plan_response(
    plan: ResearchPlan,
    *,
    source: str,
    planner_error: str | None,
) -> ResearchPlanResponse:
    return ResearchPlanResponse(
        question_type=plan.question_type,
        strategy=plan.strategy,
        core_claims=[
            ResearchClaimTargetResponse(
                claim_id=value.claim_id,
                statement=value.statement,
                claim_type=value.claim_type,
                priority=value.priority,
                falsifiable_prediction=value.falsifiable_prediction,
                required_evidence_types=value.required_evidence_types,
                status=value.status,
                evidence_ids=value.evidence_ids,
                contradicting_evidence_ids=value.contradicting_evidence_ids,
                unresolved_reason=value.unresolved_reason,
            )
            for value in plan.core_claims
        ],
        required_subqueries=plan.required_subqueries,
        exploratory_subqueries=plan.exploratory_subqueries,
        counterevidence_subqueries=plan.counterevidence_subqueries,
        allowed_sources=plan.allowed_sources,
        allowed_actions=list(plan.allowed_actions),
        max_rounds=plan.max_rounds,
        max_total_subqueries=plan.max_total_subqueries,
        max_actions_per_round=plan.max_actions_per_round,
        max_external_requests=plan.max_external_requests,
        source=source,
        planner_error=planner_error,
    )


def _audit_response(
    trail: ResearchAuditTrail,
) -> tuple[
    ResearchPlanResponse | None,
    list[ResearchActionResponse],
    list[SufficiencyVerdictResponse],
    list[ResearchProgressStepResponse],
]:
    latest = trail.plans[-1] if trail.plans else None
    research_plan = None
    if latest is not None:
        plan = ResearchPlan.model_validate(latest.plan_data)
        research_plan = _research_plan_response(
            plan,
            source=latest.source,
            planner_error=None,
        )
    actions = [
        ResearchActionResponse.model_validate(
            {
                **value.proposal,
                "round_number": value.round_number,
                "decision_score": value.decision_score,
                "approved": value.approved,
                "rejection_reason": value.rejection_reason,
            }
        )
        for value in trail.actions
    ]
    sufficiency = [
        SufficiencyVerdictResponse.model_validate(
            {**value.verdict_data, "round_number": value.round_number}
        )
        for value in trail.sufficiency
    ]
    progress = [
        ResearchProgressStepResponse(
            step_number=value.step_number,
            round_number=value.round_number,
            step_type=value.step_type,
            decision=value.decision,
            status=value.status,
        )
        for value in trail.steps
    ]
    return research_plan, actions, sufficiency, progress


@router.get("/model-status", response_model=ModelStatusResponse)
async def model_status(
    session: SessionDependency,
    settings: SettingsDependency,
) -> ModelStatusResponse:
    model_settings = await resolve_model_settings(session, settings)
    configured = model_settings_configured(model_settings)
    return ModelStatusResponse(
        configured=configured,
        provider=model_settings.llm_provider if configured else None,
        model=model_settings.llm_model if configured else None,
    )


@router.post("/answer", response_model=ResearchAnswerResponse)
async def answer_research_question(
    request: ResearchAnswerRequest,
    http_request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> ResearchAnswerResponse:
    project = await session.get(Project, request.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if request.collection_id:
        collection = await session.get(RagCollection, request.collection_id)
        if collection is None or collection.project_id != request.project_id:
            raise HTTPException(status_code=404, detail="RAG collection not found in project")
    try:
        model_settings = await resolve_model_settings(session, settings)
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    workflow_id = uuid4()
    retrieval_config = RetrievalConfig.from_settings(settings)
    tokens = EvidenceTokenService(settings.evidence_signing_key.get_secret_value())
    research_router = DeterministicResearchRouter()
    budget = ResearchBudget.for_depth(request.model_depth)
    dynamic_slots = (
        budget.max_rounds * budget.max_actions_per_round
        if request.dynamic_planning
        else 0
    )
    base_subquery_limit = max(1, budget.max_total_subqueries - dynamic_slots)
    routing = research_router.route(
        request.question,
        preference=request.strategy,
        max_subqueries=min(request.max_subqueries, base_subquery_limit),
        max_followup_rounds=request.max_followup_rounds,
    )

    def build_adaptive(
        project_revision: int,
        *,
        same_transaction: bool = False,
    ) -> AdaptiveRetrievalService:
        return AdaptiveRetrievalService(
            cache=http_request.app.state.cache,
            token_service=tokens,
            workflow_id=workflow_id,
            project_id=request.project_id,
            collection_id=request.collection_id,
            project_revision=project_revision,
            config=retrieval_config,
            retriever_factory=lambda: SQLiteHybridRetriever(
                session,
                tokens,
                workflow_id=workflow_id,
                config=retrieval_config,
                embedding_service=get_embedding_service(),
                session_factory=None if same_transaction else async_session_factory,
                collection_id=request.collection_id,
                reranker=(
                    get_reranker_service(retrieval_config.reranker_model)
                    if retrieval_config.reranker_enabled
                    else None
                ),
            ),
            retrieve_cached=(
                retrieve_without_cache if same_transaction else retrieve_with_cache
            ),
        )

    adaptive = build_adaptive(project.retrieval_revision)
    try:
        execution = await adaptive.execute(routing, limit=request.retrieval_limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    candidates = list(execution.candidates)
    retrieval_steps = list(execution.steps)
    tool_actions: list[dict[str, object]] = [
        {
            "tool": "local_retrieval",
            "reason": f"routing:{routing.strategy}",
            "queries": [item.query for item in routing.subqueries],
            "executed": True,
            "results": len(candidates),
            "error": None,
        }
    ]
    graph_candidates = sum(
        int(route["candidates"])
        for step in retrieval_steps
        if step.report is not None
        for route in step.report["routes"]
        if str(route["route"]).startswith("graph")
    )
    citation_queries = [
        item.query for item in routing.subqueries if item.focus == "citation_landscape"
    ]
    if citation_queries:
        tool_actions.append(
            {
                "tool": "citation_graph",
                "reason": "citation_landscape_subproblem",
                "queries": citation_queries,
                "executed": True,
                "results": graph_candidates,
                "error": None,
            }
        )
    retrieval_mode: Literal[
        "identifier", "hybrid-sparse", "hybrid-dense"
    ] = derive_retrieval_mode(candidates)
    run = ResearchRun(
        project_id=request.project_id,
        question=request.question,
        language=request.language,
        status="processing",
        retrieval_mode=retrieval_mode,
        model_provider=model_settings.llm_provider or "",
        model_name=model_settings.llm_model or "",
        retrieval_config={
            **retrieval_config_payload(retrieval_config),
            "model_depth": request.model_depth,
            "max_context_tokens": request.max_context_tokens,
            "dynamic_planning": request.dynamic_planning,
            "max_external_requests": request.max_external_requests,
        },
        retrieval_trace={
            "collection_id": str(request.collection_id) if request.collection_id else None,
            "routing": routing_audit_payload(routing),
        },
        result={},
    )
    session.add(run)
    await session.flush()
    memory_context = await MemoryContextService(session).recall(
        project_id=request.project_id,
        query=request.question,
        consumer="research_planner",
        statuses=("active", "superseded", "retracted"),
        min_confidence=0.5,
        limit=16,
    )
    planning_memory = tuple(value.to_payload() for value in memory_context.items)
    audit = ResearchAuditService(session)
    parent_snapshot_id: UUID | None = None

    async def record_plan(plan: ResearchPlan, source: str, round_number: int) -> None:
        nonlocal parent_snapshot_id
        snapshot = await audit.add_plan(
            research_run_id=run.id,
            plan=plan,
            source=source,
            parent_snapshot_id=parent_snapshot_id,
        )
        parent_snapshot_id = snapshot.id
        await audit.add_claims(research_run_id=run.id, claims=plan.core_claims)
        if round_number > 0:
            await audit.add_round(
                research_run_id=run.id,
                round_number=round_number,
                phase="plan" if round_number == 1 else "replan",
                status="running",
                metrics={"plan_source": source},
            )

    async def record_step(
        round_number: int,
        step_type: str,
        decision: str,
        rationale: str,
        payload: dict[str, Any],
    ) -> None:
        proposed = payload.get("decisions", [])
        executed = payload.get("action_results", {})
        await audit.append_step(
            project_id=request.project_id,
            workflow_id=workflow_id,
            round_number=round_number,
            step_type=step_type,
            input_summary=ResearchAuditService.summary(payload),
            decision=decision,
            rationale=rationale,
            research_run_id=run.id,
            proposed_actions=(proposed if isinstance(proposed, list) else []),
            executed_actions=(
                [
                    {"action_id": key, **value}
                    for key, value in executed.items()
                    if isinstance(value, dict)
                ]
                if isinstance(executed, dict)
                else []
            ),
            evidence_ids=[str(value) for value in payload.get("evidence_ids", [])],
        )

    async def record_actions(
        round_number: int,
        decisions: tuple[ActionDecision, ...],
        results: dict[str, dict[str, Any]],
    ) -> None:
        await audit.add_actions(
            research_run_id=run.id,
            round_number=round_number,
            decisions=decisions,
            result_by_action=results,
        )

    async def record_sufficiency(
        round_number: int, verdict: SufficiencyVerdict
    ) -> None:
        await audit.add_sufficiency(
            research_run_id=run.id,
            round_number=round_number,
            verdict=verdict,
        )
        if round_number > 0:
            await audit.complete_round(
                research_run_id=run.id,
                round_number=round_number,
                verdict=verdict,
            )

    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            provider = build_model_provider(client, model_settings)
            pipeline = ResearchPipeline(
                model=provider,
                mechanical_verifier=MechanicalEvidenceVerifier(
                    tokens,
                    workflow_id=workflow_id,
                ),
                entailment_threshold=settings.claim_entailment_threshold,
            )

            async def answer_candidates(
                values: list[RetrievalCandidate], analysis: Any
            ) -> ResearchResult:
                return await pipeline.answer(
                    question=request.question,
                    language=request.language,
                    candidates=values,
                    citations=await citation_map_for_candidates(session, values),
                    model_depth=request.model_depth,
                    max_context_tokens=request.max_context_tokens,
                    analysis_override=analysis,
                )

            async def execute_actions(
                decisions: tuple[ActionDecision, ...],
                round_number: int,
            ) -> tuple[list[RetrievalCandidate], dict[str, dict[str, Any]]]:
                nonlocal adaptive, project
                output: list[RetrievalCandidate] = []
                results: dict[str, dict[str, Any]] = {}
                local = tuple(
                    value
                    for value in decisions
                    if value.action.action_type
                    not in {"scholarly_discovery", "controlled_web_search"}
                )
                if local:
                    local_execution = await adaptive.execute_actions(
                        routing,
                        actions=tuple(
                            (
                                value.action.action_id,
                                value.action.action_type,
                                value.action.query,
                            )
                            for value in local
                        ),
                        limit=request.retrieval_limit,
                        round_number=round_number,
                    )
                    retrieval_steps.extend(local_execution.steps)
                    output.extend(local_execution.candidates)
                    for value in local:
                        count = sum(
                            len(step.candidates)
                            for step in local_execution.steps
                            if step.subquery_id == value.action.action_id
                        )
                        results[value.action.action_id] = {
                            "evidence_candidates": count,
                        }
                        tool_actions.append(
                            {
                                "tool": (
                                    "citation_graph"
                                    if value.action.action_type == "citation_landscape"
                                    else "local_retrieval"
                                    if value.action.action_type == "local_hybrid_search"
                                    else value.action.action_type
                                ),
                                "reason": value.action.rationale or "approved_dynamic_action",
                                "queries": [value.action.query],
                                "executed": True,
                                "results": count,
                                "error": None,
                            }
                        )
                for value in decisions:
                    if value.action.action_type == "scholarly_discovery":
                        savepoint = await session.begin_nested()
                        try:
                            imported = await _supplement_scholarly(
                                session,
                                settings,
                                project_id=request.project_id,
                                queries=(value.action.query,),
                            )
                            await savepoint.commit()
                            await session.refresh(project)
                            adaptive = build_adaptive(
                                project.retrieval_revision,
                                same_transaction=True,
                            )
                            followup = await adaptive.execute_actions(
                                routing,
                                actions=(
                                    (
                                        value.action.action_id,
                                        "local_hybrid_search",
                                        value.action.query,
                                    ),
                                ),
                                limit=request.retrieval_limit,
                                round_number=round_number,
                            )
                            retrieval_steps.extend(followup.steps)
                            output.extend(followup.candidates)
                            results[value.action.action_id] = {
                                "papers_imported": imported,
                                "evidence_candidates": len(followup.candidates),
                            }
                            error: str | None = None
                        except (
                            LiteratureProviderError,
                            httpx.HTTPError,
                            ValueError,
                        ) as exc:
                            await savepoint.rollback()
                            imported = 0
                            error = type(exc).__name__
                            results[value.action.action_id] = {"error": error}
                            logger.warning(
                                "research_scholarly_action_failed project_id=%s reason=%s",
                                request.project_id,
                                error,
                            )
                        tool_actions.append(
                            {
                                "tool": "scholarly_discovery",
                                "reason": value.action.rationale or "approved_dynamic_action",
                                "queries": [value.action.query],
                                "executed": True,
                                "results": imported,
                                "error": error,
                            }
                        )
                    elif value.action.action_type == "controlled_web_search":
                        web_settings = await resolve_web_search_settings(session, settings)
                        if web_settings is None:
                            web_results = []
                            web_error: str | None = "web_search_not_configured"
                        else:
                            try:
                                web_results = await TavilySearchService(
                                    client,
                                    web_settings,
                                ).search(value.action.query)
                                web_error = None
                            except WebSearchError as exc:
                                web_results = []
                                web_error = type(exc).__name__
                        results[value.action.action_id] = {
                            "context_results": len(web_results),
                            "urls": [item.url for item in web_results],
                            "external_context_not_evidence": [
                                {
                                    "title": item.title,
                                    "url": item.url,
                                    "snippet": item.snippet,
                                    "published_date": item.published_date,
                                }
                                for item in web_results
                            ],
                            "error": web_error,
                            "evidence_eligible": False,
                        }
                        tool_actions.append(
                            {
                                "tool": "controlled_web_search",
                                "reason": (
                                    "context_only_not_biomedical_evidence"
                                ),
                                "queries": [value.action.query],
                                "executed": True,
                                "results": len(web_results),
                                "error": web_error,
                            }
                        )
                return output, results

            outcome = await ControlledDynamicResearchService(
                planner=DynamicResearchPlanner(provider),
                pipeline=pipeline,
                execute_actions=execute_actions,
                answer=answer_candidates,
                record_step=record_step,
                record_plan=record_plan,
                record_actions=record_actions,
                record_sufficiency=record_sufficiency,
                max_claim_workers=(4 if request.model_depth in {"deep", "max"} else 2),
                claim_parallel_safe=not settings.database_url.startswith("sqlite"),
            ).run(
                question=request.question,
                routing=routing,
                initial_candidates=candidates,
                permissions=ResearchPermissions(
                    allow_scholarly_discovery=request.allow_pubmed_search,
                    allow_web_search=request.allow_web_search,
                    allow_auto_import=request.allow_auto_import,
                ),
                model_depth=request.model_depth,
                max_context_tokens=request.max_context_tokens,
                dynamic_planning=request.dynamic_planning,
                max_external_requests=request.max_external_requests,
                planning_context=planning_memory,
            )
            candidates = list(outcome.candidates)
            result = outcome.result
            retrieval_mode = derive_retrieval_mode(candidates)
    except ModelConfigurationError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ModelResponseError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run.status = "verified" if result.claims else "insufficient_evidence"
    run.retrieval_mode = retrieval_mode
    run.retrieval_trace = {
        "collection_id": str(request.collection_id) if request.collection_id else None,
        "routing": routing_audit_payload(routing),
        "steps": retrieval_steps_payload(retrieval_steps),
        "step_reports": [step.report for step in retrieval_steps],
        "followup_queries": [step.query for step in retrieval_steps if step.round > 0],
        "tool_actions": tool_actions,
        "plan_source": outcome.plan_source,
        "planner_errors": list(outcome.planner_errors),
        "rounds_executed": outcome.rounds_executed,
    }
    run.result = result.model_dump(mode="json")
    await audit.apply_verified_result(research_run_id=run.id, result=result)
    route_memory = RetrievalRouteMemoryService(session)
    route_scope = {
        "project_id": str(request.project_id),
        "collection_id": str(request.collection_id) if request.collection_id else None,
        "strategy": routing.strategy,
        "question_type": routing.question_type,
    }
    await route_memory.record_steps(
        project_id=request.project_id,
        workflow_id=workflow_id,
        workflow_kind="research",
        research_run_id=run.id,
        steps=retrieval_steps,
        scope=route_scope,
    )
    await route_memory.record_action_results(
        project_id=request.project_id,
        workflow_id=workflow_id,
        workflow_kind="research",
        research_run_id=run.id,
        round_number=outcome.rounds_executed,
        actions=tool_actions,
        scope=route_scope,
    )
    usage_service = UsageService(session)
    await usage_service.record_model_events(
        project_id=request.project_id,
        research_run_id=run.id,
        events=drain_model_usage(provider),
    )
    for step in retrieval_steps:
        await usage_service.record_retrieval(
            project_id=request.project_id,
            operation=f"research.retrieval.{step.focus}",
            cache_level=step.cache_level,
        )
    await session.commit()
    trail = await audit.trail(run.id)
    research_plan, research_actions, sufficiency, progress = _audit_response(trail)
    if research_plan is not None:
        research_plan = research_plan.model_copy(
            update={
                "source": outcome.plan_source,
                "planner_error": (
                    "; ".join(outcome.planner_errors)
                    if outcome.planner_errors
                    else None
                ),
            }
        )
    return ResearchAnswerResponse(
        run_id=run.id,
        retrieval_mode=retrieval_mode,
        retrieval_version=retrieval_config.version,
        routing=ResearchRoutingDecisionResponse.model_validate(
            routing_audit_payload(routing)
        ),
        retrieval_steps=[
            RetrievalStepAuditResponse.model_validate(value)
            for value in retrieval_steps_payload(retrieval_steps)
        ],
        tool_actions=[
            ResearchToolActionResponse.model_validate(value) for value in tool_actions
        ],
        research_plan=research_plan,
        research_actions=research_actions,
        sufficiency=sufficiency,
        progress=progress,
        result=result,
    )


@router.get("/{run_id}", response_model=ResearchAnswerResponse)
async def get_research_run(run_id: UUID, session: SessionDependency) -> ResearchAnswerResponse:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    research_plan, research_actions, sufficiency, progress = _audit_response(
        await ResearchAuditService(session).trail(run_id)
    )
    if research_plan is not None:
        planner_errors = run.retrieval_trace.get("planner_errors", [])
        research_plan = research_plan.model_copy(
            update={
                "source": str(
                    run.retrieval_trace.get("plan_source", research_plan.source)
                ),
                "planner_error": (
                    "; ".join(str(value) for value in planner_errors)
                    if isinstance(planner_errors, list) and planner_errors
                    else None
                ),
            }
        )
    return ResearchAnswerResponse(
        run_id=run.id,
        retrieval_mode=run.retrieval_mode,
        retrieval_version=str(run.retrieval_config.get("version", "unknown")),
        routing=(
            ResearchRoutingDecisionResponse.model_validate(
                run.retrieval_trace["routing"]
            )
            if run.retrieval_trace.get("routing")
            else None
        ),
        retrieval_steps=[
            RetrievalStepAuditResponse.model_validate(value)
            for value in run.retrieval_trace.get("steps", [])
        ],
        tool_actions=[
            ResearchToolActionResponse.model_validate(value)
            for value in run.retrieval_trace.get("tool_actions", [])
        ],
        research_plan=research_plan,
        research_actions=research_actions,
        sufficiency=sufficiency,
        progress=progress,
        result=ResearchResult.model_validate(run.result),
    )


@router.get("/{run_id}/trace", response_model=ResearchTraceResponse)
async def get_research_trace(
    run_id: UUID,
    session: SessionDependency,
) -> ResearchTraceResponse:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    trail = await ResearchAuditService(session).trail(run_id)
    trace = build_research_trace(trail)
    return ResearchTraceResponse(
        run_id=run_id,
        plan_versions=[
            ResearchPlanVersionResponse(
                version_number=value.version_number,
                source=value.source,
                parent_version_number=value.parent_version_number,
                added_claim_ids=list(value.added_claim_ids),
                removed_claim_ids=list(value.removed_claim_ids),
                added_action_ids=list(value.added_action_ids),
                removed_action_ids=list(value.removed_action_ids),
                added_queries=list(value.added_queries),
            )
            for value in trace.plans
        ],
        rounds=[
            ResearchRoundTraceResponse(
                round_number=value.round_number,
                phase=value.phase,
                status=value.status,
                approved_actions=value.approved_actions,
                rejected_actions=value.rejected_actions,
                external_actions=value.external_actions,
                action_budget_used=value.action_budget_used,
                action_budget_limit=value.action_budget_limit,
                external_budget_used=value.external_budget_used,
                external_budget_limit=value.external_budget_limit,
                new_evidence_count=value.new_evidence_count,
                sufficient=value.sufficient,
                stop_reason=value.stop_reason,
            )
            for value in trace.rounds
        ],
        claim_evidence_matrix=[
            ClaimEvidenceMatrixRowResponse(
                claim_key=value.claim_key,
                statement=value.statement,
                status=value.status,
                verification_label=value.verification_label,
                verification_confidence=value.verification_confidence,
                evidence_ids=list(value.evidence_ids),
                contradicting_evidence_ids=list(value.contradicting_evidence_ids),
            )
            for value in trace.claims
        ],
        progress=[
            ResearchProgressStepResponse(
                step_number=value.step_number,
                round_number=value.round_number,
                step_type=value.step_type,
                decision=value.decision,
                status=value.status,
            )
            for value in trail.steps
        ],
    )


@router.get("/{run_id}/review", response_model=ResearchReviewResponse)
async def get_research_review(
    run_id: UUID,
    session: SessionDependency,
) -> ResearchReviewResponse:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    markdown, citation_count = traceable_review_markdown(run)
    result = ResearchResult.model_validate(run.result)
    return ResearchReviewResponse(
        run_id=run.id,
        markdown=markdown,
        verified_claim_count=len(result.claims),
        citation_sentence_count=citation_count,
    )


@router.get("/{run_id}/audit.json")
async def export_research_audit_json(
    run_id: UUID,
    session: SessionDependency,
) -> Response:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    trail = await ResearchAuditService(session).trail(run_id)
    payload = json.dumps(
        trace_audit_package(run, trail),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="research-{run_id}-audit.json"'
        },
    )


@router.post("/{run_id}/publish-facts", response_model=ResearchFactsPublishResponse)
async def publish_research_facts(
    run_id: UUID,
    payload: ResearchFactsPublishRequest,
    session: SessionDependency,
) -> ResearchFactsPublishResponse:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    try:
        result = await ResearchFactPublisher(session).publish(
            run=run,
            published_by=payload.published_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return ResearchFactsPublishResponse(
        run_id=run.id,
        created=result.created,
        reused=result.reused,
        conflicts=result.conflicts,
        fact_ids=list(result.fact_ids),
        published_at=result.published_at,
    )


@router.get("/{run_id}/export")
async def export_research_run(
    run_id: UUID,
    session: SessionDependency,
    format: Literal["markdown", "review", "docx", "ris", "bibtex"] = "markdown",
) -> Response:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    if format == "docx":
        docx_content = research_result_docx(run.question, run.result)
        return Response(
            content=docx_content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="research-{run_id}.docx"'},
        )
    if format == "review":
        review_content, _ = traceable_review_markdown(run)
        return Response(
            content=review_content,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="research-{run_id}-review.md"'
            },
        )
    if format in {"ris", "bibtex"}:
        papers = await _export_referenced_papers(session, run.result)
        if format == "ris":
            content = research_result_ris(run.question, run.result, papers)
            media_type = "application/x-research-info-systems; charset=utf-8"
            extension = "ris"
        else:
            content = research_result_bibtex(run.question, run.result, papers)
            media_type = "application/x-bibtex; charset=utf-8"
            extension = "bib"
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="research-{run_id}.{extension}"'
            },
        )
    markdown_content = research_result_markdown(run.question, run.result)
    return Response(
        content=markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="research-{run_id}.md"'},
    )


async def _export_referenced_papers(
    session: AsyncSession, result: dict[str, Any]
) -> list[dict[str, Any]]:
    """Resolve the papers referenced by a research result from the local library."""
    pmids: list[str] = []
    dois: list[str] = []
    for claim in result.get("claims", []):
        for evidence in claim.get("evidence", []):
            locator = evidence.get("source_locator") or {}
            if value := str(locator.get("pmid") or ""):
                pmids.append(value)
            if value := str(locator.get("doi") or ""):
                dois.append(value)
    normalized_dois = [value for value in (normalize_doi(doi) for doi in dois) if value]
    conditions = []
    if pmids:
        conditions.append(Paper.pmid.in_(pmids))
    if normalized_dois:
        conditions.append(Paper.doi_normalized.in_(normalized_dois))
    if not conditions:
        return []
    papers = (
        await session.scalars(select(Paper).where(or_(*conditions)))
    ).all()
    return [
        {
            "pmid": paper.pmid,
            "doi": paper.doi,
            "title": paper.title,
            "journal": paper.journal,
            "publication_year": paper.publication_year,
            "authors": paper.authors,
        }
        for paper in papers
    ]


@router.delete("/{run_id}", status_code=204)
async def delete_research_run(
    run_id: UUID,
    session: SessionDependency,
) -> Response:
    run = await session.get(ResearchRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Research run not found")
    run.deleted_at = datetime.now(UTC)
    run.delete_reason = "user_deleted"
    await session.commit()
    return Response(status_code=204)
