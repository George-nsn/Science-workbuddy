import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.providers import ModelProvider, StructuredGenerationRequest
from science_buddy.services.research import ResearchResult
from science_buddy.services.research_routing import ResearchRoutingDecision
from science_buddy.services.token_budget import fit_evidence_payload
from science_buddy.services.tool_registry import RESEARCH_ACTION_TOOL_IDS

ActionType = Literal[
    "local_hybrid_search",
    "graph_local_search",
    "graph_global_search",
    "graph_drift_search",
    "graph_path_search",
    "citation_landscape",
    "scholarly_discovery",
    "controlled_web_search",
    "stop_research",
]
ClaimStatus = Literal[
    "proposed",
    "investigating",
    "supported",
    "partially_supported",
    "contradicted",
    "unverified_hypothesis",
    "deferred",
]
_ALLOWED_ACTION_TYPES = frozenset({*RESEARCH_ACTION_TOOL_IDS, "stop_research"})
_PERMISSION_GATED_ACTION_TYPES = frozenset(
    {"scholarly_discovery", "controlled_web_search"}
)


class InvestigationClaim(BaseModel):
    claim_id: str = Field(min_length=1, max_length=64)
    statement: str = Field(min_length=2, max_length=1500)
    claim_type: str = Field(default="hypothesis", max_length=64)
    priority: float = Field(default=0.5, ge=0, le=1)
    falsifiable_prediction: str = Field(default="", max_length=1500)
    required_evidence_types: list[str] = Field(default_factory=list, max_length=10)
    status: ClaimStatus = "proposed"
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = Field(default=None, max_length=1000)


class ProposedResearchAction(BaseModel):
    action_id: str = Field(min_length=1, max_length=64)
    action_type: ActionType
    query: str = Field(default="", max_length=500)
    target_claim_ids: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(default="", max_length=1000)
    expected_information_gain: float = Field(default=0.5, ge=0, le=1)
    novelty: float = Field(default=0.5, ge=0, le=1)
    falsifiability: float = Field(default=0.5, ge=0, le=1)
    estimated_cost: float = Field(default=0.5, ge=0, le=1)
    requested_source: str = Field(default="local", max_length=64)
    requires_external_access: bool = False

    @field_validator("action_type", mode="before")
    @classmethod
    def reject_unknown_action_type(cls, value: object) -> object:
        if not isinstance(value, str) or value not in _ALLOWED_ACTION_TYPES:
            raise ValueError("unknown research action type")
        return value


class ResearchPlan(BaseModel):
    question_type: str
    strategy: str
    core_claims: list[InvestigationClaim] = Field(default_factory=list, max_length=12)
    required_subqueries: list[str] = Field(default_factory=list, max_length=10)
    exploratory_subqueries: list[str] = Field(default_factory=list, max_length=10)
    counterevidence_subqueries: list[str] = Field(default_factory=list, max_length=6)
    proposed_actions: list[ProposedResearchAction] = Field(default_factory=list, max_length=20)
    allowed_sources: list[str] = Field(default_factory=list)
    allowed_actions: list[ActionType] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list, max_length=12)
    max_rounds: int = Field(default=1, ge=0, le=2)
    max_total_subqueries: int = Field(default=6, ge=1, le=20)
    max_actions_per_round: int = Field(default=2, ge=0, le=4)
    max_external_requests: int = Field(default=0, ge=0, le=8)
    stop_conditions: list[str] = Field(default_factory=list, max_length=12)
    safety_constraints: list[str] = Field(default_factory=list, max_length=12)


class SufficiencyVerdict(BaseModel):
    answer_coverage: float = Field(default=0, ge=0, le=1)
    evidence_quality: float = Field(default=0, ge=0, le=1)
    counterevidence_coverage: float = Field(default=0, ge=0, le=1)
    alternative_hypothesis_coverage: float = Field(default=0, ge=0, le=1)
    population_coverage: float = Field(default=0, ge=0, le=1)
    methodological_coverage: float = Field(default=0, ge=0, le=1)
    novelty_coverage: float = Field(default=0, ge=0, le=1)
    unresolved_claim_ids: list[str] = Field(default_factory=list)
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    proposed_followups: list[str] = Field(default_factory=list, max_length=2)
    sufficient: bool = False
    stop_reason: str = Field(default="evidence_gap", max_length=1000)


class ExplorationProposal(BaseModel):
    claims: list[InvestigationClaim] = Field(default_factory=list, max_length=12)
    actions: list[ProposedResearchAction] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=2000)


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    max_rounds: int
    max_total_subqueries: int
    max_actions_per_round: int
    max_external_requests: int
    required_counterevidence: int
    allow_replan: bool

    @classmethod
    def for_depth(cls, depth: str) -> "ResearchBudget":
        return {
            "quick": cls(0, 2, 0, 1, 0, False),
            "balanced": cls(1, 6, 2, 2, 1, False),
            "deep": cls(2, 10, 4, 4, 2, True),
            "max": cls(2, 10, 4, 4, 2, True),
        }.get(depth, cls(1, 6, 2, 2, 1, False))


@dataclass(frozen=True, slots=True)
class ResearchPermissions:
    allow_scholarly_discovery: bool
    allow_web_search: bool
    allow_auto_import: bool


@dataclass(frozen=True, slots=True)
class ActionDecision:
    action: ProposedResearchAction
    score: float
    approved: bool
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class DynamicResearchPlan:
    plan: ResearchPlan
    source: str
    planner_error: str | None
    decisions: tuple[ActionDecision, ...]


def deterministic_research_plan(
    question: str,
    decision: ResearchRoutingDecision,
    *,
    budget: ResearchBudget,
    permissions: ResearchPermissions,
) -> ResearchPlan:
    claims = [
        InvestigationClaim(
            claim_id="claim-core",
            statement=question,
            claim_type="core",
            priority=1.0,
            falsifiable_prediction=(
                "The available evidence should directly support, refute, or delimit "
                "the question."
            ),
            required_evidence_types=["primary_or_review_evidence"],
        )
    ]
    actions = [
        ProposedResearchAction(
            action_id=f"required-{item.subquery_id}",
            action_type="local_hybrid_search",
            query=item.query,
            target_claim_ids=["claim-core"],
            rationale=f"Deterministic required coverage: {item.focus}",
            expected_information_gain=0.9 if item.subquery_id == "q1" else 0.7,
            novelty=0.1,
            falsifiability=0.8,
            estimated_cost=0.2,
            requested_source="local",
        )
        for item in decision.subqueries
    ]
    allowed_actions: list[ActionType] = cast(
        list[ActionType],
        sorted(_ALLOWED_ACTION_TYPES - _PERMISSION_GATED_ACTION_TYPES),
    )
    allowed_sources = ["local", "graph", "citation"]
    if permissions.allow_scholarly_discovery:
        allowed_actions.append("scholarly_discovery")
        allowed_sources.append("scholarly")
    if permissions.allow_web_search:
        allowed_actions.append("controlled_web_search")
        allowed_sources.append("web")
    return ResearchPlan(
        question_type=decision.question_type,
        strategy=decision.strategy,
        core_claims=claims,
        required_subqueries=[item.query for item in decision.subqueries],
        exploratory_subqueries=[],
        counterevidence_subqueries=[],
        proposed_actions=actions,
        allowed_sources=allowed_sources,
        allowed_actions=allowed_actions,
        evidence_requirements=[
            "At least one current-workflow Evidence ID for each factual conclusion.",
            "Counterevidence must be checked for non-identifier questions.",
            "Conclusion strength must not exceed evidence strength.",
        ],
        max_rounds=budget.max_rounds,
        max_total_subqueries=budget.max_total_subqueries,
        max_actions_per_round=budget.max_actions_per_round,
        max_external_requests=budget.max_external_requests,
        stop_conditions=[
            "core_claims_covered",
            "counterevidence_checked",
            "no_new_high_value_evidence",
            "budget_exhausted",
        ],
        safety_constraints=[
            "No free-form tool execution.",
            "No unapproved external source.",
            "Unverified hypotheses cannot become factual conclusions.",
        ],
    )


class DynamicResearchPlanner:
    def __init__(self, model: ModelProvider) -> None:
        self._model = model

    async def propose(
        self,
        *,
        question: str,
        base_plan: ResearchPlan,
        candidates: Sequence[RetrievalCandidate],
        unresolved_claims: Sequence[InvestigationClaim] = (),
        planning_context: Sequence[dict[str, Any]] = (),
        exploration_context: Sequence[dict[str, Any]] = (),
        round_number: int,
        model_depth: Literal["quick", "balanced", "deep", "max"],
        max_context_tokens: int,
    ) -> ExplorationProposal:
        evidence: list[dict[str, Any]] = [
            {
                "evidence_id": item.evidence_id,
                "text": item.text[:1600],
                "source_locator": item.source_locator,
            }
            for item in candidates[:18]
        ]
        evidence = fit_evidence_payload(
            evidence, max_context_tokens=max_context_tokens
        )
        raw = await self._model.generate_structured(
            StructuredGenerationRequest(
                system_instruction=(
                    "You are a bounded biomedical research exploration planner. Propose "
                    "falsifiable claims, alternative mechanisms, counterevidence queries, "
                    "and actions only from allowed_actions. Never execute tools, invent "
                    "Evidence IDs, or present hypotheses as facts. Historical memory is "
                    "planning experience only: use it to avoid failed routes or repeat useful "
                    "searches, never as support for a conclusion. Return structured JSON."
                ),
                user_content=json.dumps(
                    {
                        "question": question,
                        "round": round_number,
                        "base_plan": base_plan.model_dump(mode="json"),
                        "evidence": evidence,
                        "unresolved_claims": [
                            claim.model_dump(mode="json") for claim in unresolved_claims
                        ],
                        "planning_context_not_evidence": list(planning_context[-16:]),
                        "external_context_not_evidence": list(exploration_context[-8:]),
                    },
                    ensure_ascii=False,
                ),
                response_schema=ExplorationProposal.model_json_schema(),
                operation=f"research.dynamic_planner.round_{round_number}",
                depth=model_depth,
                max_context_tokens=max_context_tokens,
            )
        )
        return ExplorationProposal.model_validate(raw)


class ResearchActionPolicy:
    def __init__(
        self,
        *,
        plan: ResearchPlan,
        budget: ResearchBudget,
        permissions: ResearchPermissions,
        executed_queries: Sequence[str] = (),
        external_requests_used: int = 0,
    ) -> None:
        self._plan = plan
        self._budget = budget
        self._permissions = permissions
        self._executed = {self._normalize(query) for query in executed_queries}
        self._external_used = external_requests_used

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_./+-]{2,}|[\u3400-\u9fff]{2,}", value.casefold()))

    def evaluate(self, actions: Sequence[ProposedResearchAction]) -> tuple[ActionDecision, ...]:
        values: list[ActionDecision] = []
        seen_queries = set(self._executed)
        seen_action_ids: set[str] = set()
        unresolved_claim_ids = {
            claim.claim_id
            for claim in self._plan.core_claims
            if claim.status
            in {"proposed", "investigating", "unverified_hypothesis"}
        }
        for action in actions:
            reason: str | None = None
            normalized = self._normalize(action.query)
            already_seen = normalized in seen_queries
            if action.action_type not in self._plan.allowed_actions:
                reason = "action_not_allowed"
            elif action.action_id in seen_action_ids:
                reason = "duplicate_action_id"
            elif (
                action.action_type != "stop_research"
                and not action.target_claim_ids
            ):
                reason = "target_claim_required"
            elif any(
                claim_id not in unresolved_claim_ids
                for claim_id in action.target_claim_ids
            ):
                reason = "target_claim_not_unresolved"
            elif (
                action.action_type == "controlled_web_search"
                and not self._permissions.allow_web_search
            ):
                reason = "web_not_authorized"
            elif (
                action.action_type == "scholarly_discovery"
                and not self._permissions.allow_scholarly_discovery
            ):
                reason = "scholarly_discovery_not_authorized"
            elif (
                action.action_type == "scholarly_discovery"
                and not self._permissions.allow_auto_import
            ):
                reason = "auto_import_not_authorized"
            elif action.action_type != "stop_research" and len(normalized) < 2:
                reason = "blank_query"
            elif already_seen:
                reason = "duplicate_query"
            relevance = self._query_relevance(action.query)
            gap_value = min(1.0, 0.2 + 0.3 * len(action.target_claim_ids))
            duplicate_penalty = 1.0 if already_seen else 0.0
            safety_penalty = 1.0 if reason in {"action_not_allowed", "web_not_authorized"} else 0.0
            score = (
                0.30 * relevance
                + 0.25 * gap_value
                + 0.15 * action.novelty
                + 0.15 * action.falsifiability
                + 0.15 * action.expected_information_gain
                - 0.20 * duplicate_penalty
                - 0.10 * action.estimated_cost
                - 0.50 * safety_penalty
            )
            approved = reason is None and score >= 0.20 and action.action_type != "stop_research"
            values.append(ActionDecision(action, round(score, 6), approved, reason))
            if approved:
                seen_queries.add(normalized)
            seen_action_ids.add(action.action_id)
        approved_values = sorted(
            (value for value in values if value.approved),
            key=lambda value: value.score,
            reverse=True,
        )
        selected_ids, selection_rejections = self._select_diverse(approved_values)
        return tuple(
            ActionDecision(
                value.action,
                value.score,
                value.approved and value.action.action_id in selected_ids,
                (
                    value.rejection_reason
                    if value.rejection_reason
                    else (
                        None
                        if value.action.action_id in selected_ids
                        else selection_rejections.get(
                            value.action.action_id,
                            "lower_value_or_diversity_budget",
                        )
                    )
                ),
            )
            for value in values
        )

    def _query_relevance(self, query: str) -> float:
        plan_terms = self._tokens(" ".join(self._plan.required_subqueries))
        query_terms = self._tokens(query)
        if not query_terms:
            return 0.0
        return min(1.0, len(plan_terms & query_terms) / max(1, len(query_terms)))

    def _select_diverse(
        self, approved: Sequence[ActionDecision]
    ) -> tuple[set[str], dict[str, str]]:
        selected: list[ActionDecision] = []
        rejections: dict[str, str] = {}
        external_used = self._external_used
        for candidate in approved:
            if len(selected) >= self._budget.max_actions_per_round:
                rejections[candidate.action.action_id] = "action_budget_exhausted"
                continue
            is_external = candidate.action.action_type in {
                "scholarly_discovery",
                "controlled_web_search",
            }
            if is_external and external_used >= self._budget.max_external_requests:
                rejections[candidate.action.action_id] = (
                    "external_request_budget_exhausted"
                )
                continue
            candidate_terms = self._tokens(candidate.action.query)
            if any(
                self._jaccard(candidate_terms, self._tokens(value.action.query)) >= 0.85
                for value in selected
            ):
                rejections[candidate.action.action_id] = (
                    "lower_value_or_diversity_budget"
                )
                continue
            selected.append(candidate)
            if is_external:
                external_used += 1
        return {value.action.action_id for value in selected}, rejections

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 1.0


class SufficiencyJudge:
    @staticmethod
    def assess(
        *,
        plan: ResearchPlan,
        result: ResearchResult,
        round_number: int,
        budget: ResearchBudget,
        new_evidence_count: int,
        new_context_count: int = 0,
    ) -> SufficiencyVerdict:
        claim_count = max(1, len(plan.core_claims))
        answer_coverage = min(1.0, len(result.claims) / claim_count)
        evidence_quality = min(
            1.0,
            sum(len(claim.evidence) for claim in result.claims) / claim_count,
        )
        counter_coverage = 1.0 if result.conflicts else (0.5 if round_number > 0 else 0.0)
        alternative_coverage = 1.0 if not result.gaps else max(0.0, 1.0 - 0.2 * len(result.gaps))
        sufficient = (
            answer_coverage >= 0.8
            and evidence_quality >= 0.8
            and counter_coverage >= (0.5 if budget.required_counterevidence else 0.0)
        )
        budget_exhausted = round_number >= budget.max_rounds
        no_gain = (
            round_number > 0
            and new_evidence_count == 0
            and new_context_count == 0
        )
        if sufficient:
            reason = "core_claims_and_counterevidence_covered"
        elif budget_exhausted:
            reason = "round_budget_exhausted"
        elif no_gain:
            reason = "no_new_evidence"
        else:
            reason = "high_value_evidence_gaps_remain"
        return SufficiencyVerdict(
            answer_coverage=answer_coverage,
            evidence_quality=evidence_quality,
            counterevidence_coverage=counter_coverage,
            alternative_hypothesis_coverage=alternative_coverage,
            population_coverage=answer_coverage,
            methodological_coverage=evidence_quality,
            novelty_coverage=min(1.0, 0.5 + 0.1 * round_number),
            unresolved_claim_ids=[
                claim.claim_id for claim in plan.core_claims if not result.claims
            ],
            conflicting_claim_ids=[],
            proposed_followups=result.followup_queries[:2],
            sufficient=sufficient,
            stop_reason=reason,
        )


def merge_plan_exploration(base: ResearchPlan, proposal: ExplorationProposal) -> ResearchPlan:
    claim_by_id = {claim.claim_id: claim for claim in base.core_claims}
    for claim in proposal.claims:
        claim_by_id.setdefault(claim.claim_id, claim)
    action_by_id = {action.action_id: action for action in base.proposed_actions}
    for action in proposal.actions:
        action_by_id.setdefault(action.action_id, action)
    exploratory = list(base.exploratory_subqueries)
    counter = list(base.counterevidence_subqueries)
    for action in proposal.actions:
        if action.action_type == "stop_research" or not action.query:
            continue
        target = (
            counter
            if "counter" in action.action_id.casefold() or "反证" in action.rationale
            else exploratory
        )
        if action.query not in target:
            target.append(action.query)
    return base.model_copy(
        update={
            "core_claims": list(claim_by_id.values())[:12],
            "exploratory_subqueries": exploratory[:10],
            "counterevidence_subqueries": counter[:6],
            "proposed_actions": list(action_by_id.values())[:20],
        }
    )


def ensure_exploration_requirements(
    proposal: ExplorationProposal,
    *,
    question: str,
    round_number: int,
    budget: ResearchBudget,
) -> ExplorationProposal:
    actions = list(proposal.actions)
    counter_count = sum(
        "counter" in action.action_id.casefold()
        or "contradict" in action.query.casefold()
        or "反证" in action.query
        for action in actions
    )
    while counter_count < budget.required_counterevidence:
        index = counter_count + 1
        actions.append(
            ProposedResearchAction(
                action_id=f"counter-r{round_number}-{index}",
                action_type="local_hybrid_search",
                query=(
                    f"{question} contradictory evidence null findings alternative explanation "
                    f"counterevidence {index}"
                ),
                target_claim_ids=["claim-core"],
                rationale="强制反证覆盖：查找否定结果、替代解释和边界条件。",
                expected_information_gain=0.8,
                novelty=0.5,
                falsifiability=0.9,
                estimated_cost=0.2,
                requested_source="local",
            )
        )
        counter_count += 1
    return proposal.model_copy(update={"actions": actions[:20]})


def deterministic_exploration_fallback(
    *,
    question: str,
    round_number: int,
    budget: ResearchBudget,
) -> ExplorationProposal:
    return ensure_exploration_requirements(
        ExplorationProposal(
            claims=[],
            actions=[
                ProposedResearchAction(
                    action_id=f"alternative-r{round_number}",
                    action_type="local_hybrid_search",
                    query=f"{question} alternative mechanism competing hypothesis",
                    target_claim_ids=["claim-core"],
                    rationale="规划模型不可用时的确定性替代机制检索。",
                    expected_information_gain=0.65,
                    novelty=0.45,
                    falsifiability=0.7,
                    estimated_cost=0.2,
                    requested_source="local",
                )
            ],
            rationale="deterministic_planner_fallback",
        ),
        question=question,
        round_number=round_number,
        budget=budget,
    )


def plan_hash(plan: ResearchPlan) -> str:
    return hashlib.sha256(
        json.dumps(plan.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def evidence_ids(candidates: Sequence[RetrievalCandidate]) -> list[str]:
    return list(dict.fromkeys(item.evidence_id for item in candidates))


def action_payload(decision: ActionDecision) -> dict[str, Any]:
    return {
        **decision.action.model_dump(mode="json"),
        "decision_score": decision.score,
        "approved": decision.approved,
        "rejection_reason": decision.rejection_reason,
    }
