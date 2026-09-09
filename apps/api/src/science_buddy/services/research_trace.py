import json
from dataclasses import dataclass
from typing import Any

from science_buddy.infrastructure.models import ResearchRun
from science_buddy.services.dynamic_research import ResearchPlan
from science_buddy.services.research import ResearchResult
from science_buddy.services.research_audit import ResearchAuditTrail


@dataclass(frozen=True, slots=True)
class PlanVersionDiff:
    version_number: int
    source: str
    parent_version_number: int | None
    added_claim_ids: tuple[str, ...]
    removed_claim_ids: tuple[str, ...]
    added_action_ids: tuple[str, ...]
    removed_action_ids: tuple[str, ...]
    added_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoundTrace:
    round_number: int
    phase: str
    status: str
    approved_actions: int
    rejected_actions: int
    external_actions: int
    action_budget_used: int
    action_budget_limit: int
    external_budget_used: int
    external_budget_limit: int
    new_evidence_count: int
    sufficient: bool | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class ClaimEvidenceMatrixRow:
    claim_key: str
    statement: str
    status: str
    verification_label: str | None
    verification_confidence: float | None
    evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchTraceBundle:
    plans: tuple[PlanVersionDiff, ...]
    rounds: tuple[RoundTrace, ...]
    claims: tuple[ClaimEvidenceMatrixRow, ...]


def _identities(plan: ResearchPlan) -> tuple[set[str], set[str], set[str]]:
    claims = {value.claim_id for value in plan.core_claims}
    actions = {value.action_id for value in plan.proposed_actions}
    queries = {
        *plan.required_subqueries,
        *plan.exploratory_subqueries,
        *plan.counterevidence_subqueries,
    }
    return claims, actions, queries


def build_research_trace(trail: ResearchAuditTrail) -> ResearchTraceBundle:
    snapshots = {value.id: value for value in trail.plans}
    versions: list[PlanVersionDiff] = []
    for value in trail.plans:
        current = ResearchPlan.model_validate(value.plan_data)
        current_claims, current_actions, current_queries = _identities(current)
        parent = (
            snapshots.get(value.parent_snapshot_id)
            if value.parent_snapshot_id is not None
            else None
        )
        if parent is None:
            previous_claims: set[str] = set()
            previous_actions: set[str] = set()
            previous_queries: set[str] = set()
            parent_version = None
        else:
            previous = ResearchPlan.model_validate(parent.plan_data)
            previous_claims, previous_actions, previous_queries = _identities(previous)
            parent_version = parent.version_number
        versions.append(
            PlanVersionDiff(
                version_number=value.version_number,
                source=value.source,
                parent_version_number=parent_version,
                added_claim_ids=tuple(sorted(current_claims - previous_claims)),
                removed_claim_ids=tuple(sorted(previous_claims - current_claims)),
                added_action_ids=tuple(sorted(current_actions - previous_actions)),
                removed_action_ids=tuple(sorted(previous_actions - current_actions)),
                added_queries=tuple(sorted(current_queries - previous_queries)),
            )
        )
    decisions_by_round: dict[int, list[Any]] = {}
    for action in trail.actions:
        decisions_by_round.setdefault(action.round_number, []).append(action)
    sufficiency_by_round = {value.round_number: value for value in trail.sufficiency}
    evidence_by_round: dict[int, int] = {}
    for step in trail.steps:
        if step.step_type != "retrieve":
            continue
        try:
            payload = json.loads(step.input_summary)
        except json.JSONDecodeError:
            payload = {}
        evidence_by_round[step.round_number] = int(payload.get("new_evidence_count", 0))
    round_values: list[RoundTrace] = []
    latest_plan = (
        ResearchPlan.model_validate(trail.plans[-1].plan_data)
        if trail.plans
        else None
    )
    cumulative_external = 0
    for round_value in trail.rounds:
        decisions = decisions_by_round.get(round_value.round_number, [])
        sufficiency = sufficiency_by_round.get(round_value.round_number)
        external_actions = sum(
            item.approved
            and item.action_type
            in {"scholarly_discovery", "controlled_web_search"}
            for item in decisions
        )
        cumulative_external += external_actions
        round_values.append(
            RoundTrace(
                round_number=round_value.round_number,
                phase=round_value.phase,
                status=round_value.status,
                approved_actions=sum(item.approved for item in decisions),
                rejected_actions=sum(not item.approved for item in decisions),
                external_actions=external_actions,
                action_budget_used=sum(item.approved for item in decisions),
                action_budget_limit=(
                    latest_plan.max_actions_per_round if latest_plan else 0
                ),
                external_budget_used=cumulative_external,
                external_budget_limit=(
                    latest_plan.max_external_requests if latest_plan else 0
                ),
                new_evidence_count=evidence_by_round.get(
                    round_value.round_number, 0
                ),
                sufficient=sufficiency.sufficient if sufficiency else None,
                stop_reason=sufficiency.stop_reason if sufficiency else None,
            )
        )
    claims = tuple(
        ClaimEvidenceMatrixRow(
            claim_key=value.claim_key,
            statement=value.statement,
            status=value.status,
            verification_label=value.verification_label,
            verification_confidence=value.verification_confidence,
            evidence_ids=tuple(value.evidence_ids),
            contradicting_evidence_ids=tuple(value.contradicting_evidence_ids),
        )
        for value in trail.claims
    )
    return ResearchTraceBundle(tuple(versions), tuple(round_values), claims)


def trace_audit_package(run: ResearchRun, trail: ResearchAuditTrail) -> dict[str, Any]:
    trace = build_research_trace(trail)
    return {
        "schema_version": "science-buddy-research-audit-v1",
        "run": {
            "id": str(run.id),
            "project_id": str(run.project_id),
            "question": run.question,
            "language": run.language,
            "status": run.status,
            "retrieval_mode": run.retrieval_mode,
            "model_provider": run.model_provider,
            "model_name": run.model_name,
            "retrieval_config": run.retrieval_config,
            "retrieval_trace": run.retrieval_trace,
            "facts_published_at": (
                run.facts_published_at.isoformat() if run.facts_published_at else None
            ),
        },
        "plan_versions": [
            {
                "version_number": value.version_number,
                "source": value.source,
                "parent_version_number": value.parent_version_number,
                "added_claim_ids": list(value.added_claim_ids),
                "removed_claim_ids": list(value.removed_claim_ids),
                "added_action_ids": list(value.added_action_ids),
                "removed_action_ids": list(value.removed_action_ids),
                "added_queries": list(value.added_queries),
            }
            for value in trace.plans
        ],
        "rounds": [
            {
                "round_number": value.round_number,
                "phase": value.phase,
                "status": value.status,
                "approved_actions": value.approved_actions,
                "rejected_actions": value.rejected_actions,
                "external_actions": value.external_actions,
                "action_budget_used": value.action_budget_used,
                "action_budget_limit": value.action_budget_limit,
                "external_budget_used": value.external_budget_used,
                "external_budget_limit": value.external_budget_limit,
                "new_evidence_count": value.new_evidence_count,
                "sufficient": value.sufficient,
                "stop_reason": value.stop_reason,
            }
            for value in trace.rounds
        ],
        "claim_evidence_matrix": [
            {
                "claim_key": value.claim_key,
                "statement": value.statement,
                "status": value.status,
                "verification_label": value.verification_label,
                "verification_confidence": value.verification_confidence,
                "evidence_ids": list(value.evidence_ids),
                "contradicting_evidence_ids": list(value.contradicting_evidence_ids),
            }
            for value in trace.claims
        ],
        "actions": [
            {
                "round_number": value.round_number,
                "action_key": value.action_key,
                "action_type": value.action_type,
                "query": value.query,
                "approved": value.approved,
                "rejection_reason": value.rejection_reason,
                "status": value.status,
                "result": value.result,
                "error": value.error,
            }
            for value in trail.actions
        ],
        "sufficiency": [value.verdict_data for value in trail.sufficiency],
        "steps": [
            {
                "step_number": value.step_number,
                "round_number": value.round_number,
                "step_type": value.step_type,
                "decision": value.decision,
                "rationale": value.rationale,
                "evidence_ids": value.evidence_ids,
                "status": value.status,
            }
            for value in trail.steps
        ],
        "result": run.result,
    }


def traceable_review_markdown(run: ResearchRun) -> tuple[str, int]:
    result = ResearchResult.model_validate(run.result)
    lines = [
        "# 可追溯研究综述",
        "",
        f"**研究问题：** {run.question}",
        "",
        "> 本综述只使用已通过机械与语义验证的 Claim；未验证假说不进入结论。",
        "",
        "## 已验证结论",
        "",
    ]
    citation_sentences = 0
    for index, claim in enumerate(result.claims, start=1):
        citations = " ".join(f"[{value.evidence_id}]" for value in claim.evidence)
        sentence = f"{index}. {claim.statement} {citations}".strip()
        lines.append(sentence)
        citation_sentences += int(bool(claim.evidence))
    if not result.claims:
        lines.append("- 当前没有通过验证的 Claim，不能生成结论性综述。")
    lines.extend(["", "## 冲突", ""])
    lines.extend(f"- {value}" for value in result.conflicts)
    if not result.conflicts:
        lines.append("- 当前已验证 Claim 中未记录直接冲突。")
    lines.extend(["", "## 局限", ""])
    lines.append("- 结论仅覆盖当前项目/集合、当前检索轮次和已纳入 Evidence。")
    lines.append("- 未被检索到、无法访问或尚未结构化的研究可能改变结论。")
    lines.extend(["", "## 证据缺口", ""])
    lines.extend(f"- {value}" for value in result.gaps)
    if not result.gaps:
        lines.append("- 当前没有结构化证据缺口记录。")
    lines.extend(["", "## Claim—Evidence 索引", ""])
    for claim in result.claims:
        lines.append(f"- {claim.statement}")
        for evidence in claim.evidence:
            citation = evidence.formatted_citation or evidence.citation_label or "来源元数据不完整"
            lines.append(f"  - {citation} [{evidence.evidence_id}]")
    return "\n".join(lines).strip() + "\n", citation_sentences
