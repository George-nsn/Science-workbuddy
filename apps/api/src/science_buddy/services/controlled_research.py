from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.services.claim_research import ControlledClaimResearchCoordinator
from science_buddy.services.dynamic_research import (
    ActionDecision,
    DynamicResearchPlanner,
    InvestigationClaim,
    ResearchActionPolicy,
    ResearchBudget,
    ResearchPermissions,
    ResearchPlan,
    SufficiencyJudge,
    SufficiencyVerdict,
    deterministic_exploration_fallback,
    deterministic_research_plan,
    ensure_exploration_requirements,
    merge_plan_exploration,
)
from science_buddy.services.models import ModelResponseError
from science_buddy.services.research import EvidenceAnalysis, ResearchPipeline, ResearchResult
from science_buddy.services.research_routing import ResearchRoutingDecision
from science_buddy.services.tool_registry import RESEARCH_ACTION_TOOL_IDS

ActionExecutor = Callable[
    [tuple[ActionDecision, ...], int],
    Awaitable[tuple[list[RetrievalCandidate], dict[str, dict[str, Any]]]],
]
AnswerExecutor = Callable[
    [list[RetrievalCandidate], EvidenceAnalysis | None],
    Awaitable[ResearchResult],
]
StepRecorder = Callable[
    [int, str, str, str, dict[str, Any]],
    Awaitable[None],
]
PlanRecorder = Callable[[ResearchPlan, str, int], Awaitable[None]]
ActionRecorder = Callable[
    [int, tuple[ActionDecision, ...], dict[str, dict[str, Any]]],
    Awaitable[None],
]
SufficiencyRecorder = Callable[[int, SufficiencyVerdict], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ControlledResearchOutcome:
    plan: ResearchPlan
    plan_source: str
    planner_errors: tuple[str, ...]
    candidates: tuple[RetrievalCandidate, ...]
    result: ResearchResult
    decisions: tuple[tuple[ActionDecision, ...], ...]
    action_results: tuple[dict[str, dict[str, Any]], ...]
    sufficiency: tuple[SufficiencyVerdict, ...]
    rounds_executed: int


class ControlledDynamicResearchService:
    """Model-proposed exploration with deterministic policy, budget, and stop controls."""

    def __init__(
        self,
        *,
        planner: DynamicResearchPlanner,
        pipeline: ResearchPipeline,
        execute_actions: ActionExecutor,
        answer: AnswerExecutor,
        record_step: StepRecorder,
        record_plan: PlanRecorder,
        record_actions: ActionRecorder | None = None,
        record_sufficiency: SufficiencyRecorder | None = None,
        max_claim_workers: int = 2,
        claim_parallel_safe: bool = True,
    ) -> None:
        self._planner = planner
        self._pipeline = pipeline
        self._execute_actions = execute_actions
        self._answer = answer
        self._record_step = record_step
        self._record_plan = record_plan
        self._record_actions = record_actions
        self._record_sufficiency = record_sufficiency
        self._claim_workers = ControlledClaimResearchCoordinator(
            execute=execute_actions,
            max_workers=max_claim_workers,
            max_depth=2,
            max_actions_per_worker=2,
            allowed_actions=RESEARCH_ACTION_TOOL_IDS,
            parallel_safe=claim_parallel_safe,
        )

    async def run(
        self,
        *,
        question: str,
        routing: ResearchRoutingDecision,
        initial_candidates: list[RetrievalCandidate],
        permissions: ResearchPermissions,
        model_depth: Literal["quick", "balanced", "deep", "max"],
        max_context_tokens: int,
        dynamic_planning: bool,
        max_external_requests: int,
        planning_context: tuple[dict[str, Any], ...] = (),
    ) -> ControlledResearchOutcome:
        budget = ResearchBudget.for_depth(model_depth)
        budget = ResearchBudget(
            max_rounds=budget.max_rounds,
            max_total_subqueries=budget.max_total_subqueries,
            max_actions_per_round=budget.max_actions_per_round,
            max_external_requests=min(budget.max_external_requests, max_external_requests),
            required_counterevidence=budget.required_counterevidence,
            allow_replan=budget.allow_replan,
        )
        plan = deterministic_research_plan(
            question,
            routing,
            budget=budget,
            permissions=permissions,
        )
        plan_source = "deterministic"
        candidates = list(initial_candidates)
        executed_queries = list(plan.required_subqueries)
        external_requests_used = 0
        planner_errors: list[str] = []
        decisions_by_round: list[tuple[ActionDecision, ...]] = []
        action_results_by_round: list[dict[str, dict[str, Any]]] = []
        sufficiency_values: list[SufficiencyVerdict] = []
        hypotheses: list[str] = []
        exploration_context: list[dict[str, Any]] = []
        previous_evidence_ids = {item.evidence_id for item in candidates}

        await self._record_step(
            0,
            "route",
            "deterministic_base_plan",
            "最低覆盖骨架已生成；模型没有工具权限。",
            {
                "plan": plan.model_dump(mode="json"),
                "planning_memory_entity_ids": [
                    str(value.get("entity_id"))
                    for value in planning_context
                    if value.get("entity_id")
                ],
                "planning_memory_evidence_eligible": False,
            },
        )
        await self._record_plan(plan, "deterministic", 0)

        if not dynamic_planning or budget.max_rounds == 0:
            static_result = await self._answer(candidates, None)
            verdict = SufficiencyJudge.assess(
                plan=plan,
                result=static_result,
                round_number=0,
                budget=budget,
                new_evidence_count=len(candidates),
            )
            if self._record_sufficiency is not None:
                await self._record_sufficiency(0, verdict)
            await self._record_step(
                0,
                "verify",
                "mechanical_semantic_verification_complete",
                "静态路径同样由 Evidence ID 机械校验与语义支持检查控制结论。",
                {
                    "verified_claims": [claim.statement for claim in static_result.claims],
                    "verdict": verdict.model_dump(mode="json"),
                },
            )
            return ControlledResearchOutcome(
                plan=plan,
                plan_source=plan_source,
                planner_errors=(),
                candidates=tuple(candidates),
                result=static_result,
                decisions=(),
                action_results=(),
                sufficiency=(verdict,),
                rounds_executed=0,
            )

        result: ResearchResult | None = None
        for round_number in range(1, budget.max_rounds + 1):
            analysis: EvidenceAnalysis | None = None
            analysis_claims: list[InvestigationClaim] = []
            try:
                analysis = await self._pipeline.analyze(
                    question=question,
                    language="zh-CN",
                    candidates=candidates,
                    model_depth=model_depth,
                    max_context_tokens=max_context_tokens,
                )
                analysis_claims = [
                    InvestigationClaim(
                        claim_id=f"analysis-r{round_number}-{index}",
                        statement=claim.statement,
                        claim_type="evidence_claim",
                        priority=0.8,
                        falsifiable_prediction=claim.statement,
                        required_evidence_types=["current_workflow_evidence"],
                        status="investigating",
                        evidence_ids=claim.evidence_ids,
                        unresolved_reason=(
                            "; ".join(analysis.gaps[:3]) if analysis.gaps else None
                        ),
                    )
                    for index, claim in enumerate(analysis.claims, start=1)
                ]
            except (ModelResponseError, ValueError) as exc:
                planner_errors.append(
                    f"analysis_round_{round_number}:{type(exc).__name__}"
                )
            unresolved_claims = [
                *analysis_claims,
                *[
                    claim
                    for claim in plan.core_claims
                    if claim.status
                    in {"proposed", "investigating", "unverified_hypothesis"}
                ],
            ]
            proposal_source = "model"
            try:
                proposal = await self._planner.propose(
                    question=question,
                    base_plan=plan,
                    candidates=candidates,
                    unresolved_claims=unresolved_claims,
                    planning_context=planning_context,
                    exploration_context=exploration_context,
                    round_number=round_number,
                    model_depth=model_depth,
                    max_context_tokens=max_context_tokens,
                )
                proposal = ensure_exploration_requirements(
                    proposal,
                    question=question,
                    round_number=round_number,
                    budget=budget,
                )
                plan_source = "deterministic_plus_model"
            except (ModelResponseError, ValueError) as exc:
                planner_errors.append(f"round_{round_number}:{type(exc).__name__}")
                proposal_source = "fallback"
                proposal = deterministic_exploration_fallback(
                    question=question,
                    round_number=round_number,
                    budget=budget,
                )
            exploratory_claims = list(proposal.claims)
            hypotheses.extend(claim.statement for claim in exploratory_claims)
            if analysis_claims:
                proposal = proposal.model_copy(
                    update={"claims": [*analysis_claims, *exploratory_claims][:12]}
                )
            plan = merge_plan_exploration(plan, proposal)
            await self._record_plan(
                plan,
                proposal_source,
                round_number,
            )
            policy = ResearchActionPolicy(
                plan=plan,
                budget=budget,
                permissions=permissions,
                executed_queries=executed_queries,
                external_requests_used=external_requests_used,
            )
            decisions = policy.evaluate(proposal.actions)
            decisions_by_round.append(decisions)
            approved = tuple(value for value in decisions if value.approved)
            await self._record_step(
                round_number,
                "plan" if round_number == 1 else "replan",
                "model_actions_validated",
                "候选动作已通过 Schema、白名单、授权、预算和多样性校验。",
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "decisions": [self._decision_payload(value) for value in decisions],
                },
            )
            new_candidates, action_results = await self._claim_workers.execute_round(
                decisions=approved,
                round_number=round_number,
                depth=min(round_number, 2),
            )
            if self._record_actions is not None:
                await self._record_actions(round_number, decisions, action_results)
            action_results_by_round.append(action_results)
            new_context = [
                {
                    "action_id": action_id,
                    **value,
                }
                for action_id, value in action_results.items()
                if value.get("evidence_eligible") is False
            ]
            exploration_context.extend(new_context)
            executed_queries.extend(value.action.query for value in approved)
            external_requests_used += sum(
                value.action.action_type
                in {"scholarly_discovery", "controlled_web_search"}
                for value in approved
            )
            candidates = self._merge_candidates(candidates, new_candidates)
            current_evidence_ids = {item.evidence_id for item in candidates}
            new_evidence_count = len(current_evidence_ids - previous_evidence_ids)
            previous_evidence_ids = current_evidence_ids
            await self._record_step(
                round_number,
                "retrieve",
                "approved_actions_executed",
                "仅执行后端白名单动作；Web 结果不直接成为科研 Claim 证据。",
                {
                    "action_results": action_results,
                    "new_evidence_count": new_evidence_count,
                    "evidence_ids": sorted(current_evidence_ids),
                },
            )
            result = await self._answer(candidates, None)
            verdict = SufficiencyJudge.assess(
                plan=plan,
                result=result,
                round_number=round_number,
                budget=budget,
                new_evidence_count=new_evidence_count,
                new_context_count=len(new_context),
            )
            sufficiency_values.append(verdict)
            if self._record_sufficiency is not None:
                await self._record_sufficiency(round_number, verdict)
            await self._record_step(
                round_number,
                "judge",
                "stop" if verdict.sufficient else "continue_or_stop_by_budget",
                verdict.stop_reason,
                {"verdict": verdict.model_dump(mode="json")},
            )
            if verdict.sufficient:
                break
            if verdict.stop_reason in {"round_budget_exhausted", "no_new_evidence"}:
                break
            if round_number >= budget.max_rounds or not budget.allow_replan:
                break

        if result is None:
            result = await self._answer(candidates, None)
        verified_statements = {claim.statement.casefold() for claim in result.claims}
        unverified = list(
            dict.fromkeys(
                value
                for value in hypotheses
                if value.casefold() not in verified_statements
            )
        )
        result = result.model_copy(update={"unverified_hypotheses": unverified[:20]})
        await self._record_step(
            len(sufficiency_values),
            "verify",
            "mechanical_semantic_verification_complete",
            "仅通过 Evidence ID 机械校验和语义支持检查的声明进入回答主体。",
            {
                "verified_claims": [claim.statement for claim in result.claims],
                "unverified_hypotheses": result.unverified_hypotheses,
            },
        )
        return ControlledResearchOutcome(
            plan=plan,
            plan_source=plan_source,
            planner_errors=tuple(planner_errors),
            candidates=tuple(candidates),
            result=result,
            decisions=tuple(decisions_by_round),
            action_results=tuple(action_results_by_round),
            sufficiency=tuple(sufficiency_values),
            rounds_executed=len(sufficiency_values),
        )

    @staticmethod
    def _merge_candidates(
        current: list[RetrievalCandidate],
        incoming: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        by_chunk = {item.chunk_id: item for item in current}
        for item in incoming:
            existing = by_chunk.get(item.chunk_id)
            if existing is None or item.score > existing.score:
                by_chunk[item.chunk_id] = item
        return sorted(by_chunk.values(), key=lambda item: item.score, reverse=True)

    @staticmethod
    def _decision_payload(value: ActionDecision) -> dict[str, Any]:
        return {
            **value.action.model_dump(mode="json"),
            "decision_score": value.score,
            "approved": value.approved,
            "rejection_reason": value.rejection_reason,
        }
