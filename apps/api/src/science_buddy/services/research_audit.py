import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    ResearchActionAudit,
    ResearchClaimTarget,
    ResearchPlanSnapshot,
    ResearchRound,
    ResearchStepMemory,
    SufficiencyAssessment,
)
from science_buddy.services.dynamic_research import (
    ActionDecision,
    InvestigationClaim,
    ResearchPlan,
    SufficiencyVerdict,
    action_payload,
    plan_hash,
)
from science_buddy.services.research import ResearchResult


@dataclass(frozen=True, slots=True)
class StepRecord:
    id: UUID
    step_number: int


@dataclass(frozen=True, slots=True)
class ResearchAuditTrail:
    plans: tuple[ResearchPlanSnapshot, ...]
    rounds: tuple[ResearchRound, ...]
    actions: tuple[ResearchActionAudit, ...]
    claims: tuple[ResearchClaimTarget, ...]
    sufficiency: tuple[SufficiencyAssessment, ...]
    steps: tuple[ResearchStepMemory, ...]


class ResearchAuditService:
    """Append immutable research decisions and plans without replacing prior steps."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def trail(self, research_run_id: UUID) -> ResearchAuditTrail:
        plans = tuple(
            (
                await self._session.scalars(
                    select(ResearchPlanSnapshot)
                    .where(ResearchPlanSnapshot.research_run_id == research_run_id)
                    .order_by(ResearchPlanSnapshot.version_number)
                )
            ).all()
        )
        rounds = tuple(
            (
                await self._session.scalars(
                    select(ResearchRound)
                    .where(ResearchRound.research_run_id == research_run_id)
                    .order_by(ResearchRound.round_number)
                )
            ).all()
        )
        actions = tuple(
            (
                await self._session.scalars(
                    select(ResearchActionAudit)
                    .where(ResearchActionAudit.research_run_id == research_run_id)
                    .order_by(
                        ResearchActionAudit.round_number,
                        ResearchActionAudit.created_at,
                    )
                )
            ).all()
        )
        claims = tuple(
            (
                await self._session.scalars(
                    select(ResearchClaimTarget)
                    .where(ResearchClaimTarget.research_run_id == research_run_id)
                    .order_by(ResearchClaimTarget.created_at)
                )
            ).all()
        )
        sufficiency = tuple(
            (
                await self._session.scalars(
                    select(SufficiencyAssessment)
                    .where(SufficiencyAssessment.research_run_id == research_run_id)
                    .order_by(SufficiencyAssessment.round_number)
                )
            ).all()
        )
        steps = tuple(
            (
                await self._session.scalars(
                    select(ResearchStepMemory)
                    .where(ResearchStepMemory.research_run_id == research_run_id)
                    .order_by(ResearchStepMemory.step_number)
                )
            ).all()
        )
        return ResearchAuditTrail(plans, rounds, actions, claims, sufficiency, steps)

    async def add_plan(
        self,
        *,
        research_run_id: UUID,
        plan: ResearchPlan,
        source: str,
        parent_snapshot_id: UUID | None = None,
    ) -> ResearchPlanSnapshot:
        version = 1 + int(
            (
                await self._session.scalar(
                    select(func.coalesce(func.max(ResearchPlanSnapshot.version_number), 0)).where(
                        ResearchPlanSnapshot.research_run_id == research_run_id
                    )
                )
            )
            or 0
        )
        snapshot = ResearchPlanSnapshot(
            research_run_id=research_run_id,
            version_number=version,
            parent_snapshot_id=parent_snapshot_id,
            source=source,
            input_hash=plan_hash(plan),
            plan_data=plan.model_dump(mode="json"),
        )
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def add_round(
        self,
        *,
        research_run_id: UUID,
        round_number: int,
        phase: str,
        status: str,
        metrics: dict[str, Any],
    ) -> ResearchRound:
        value = ResearchRound(
            research_run_id=research_run_id,
            round_number=round_number,
            phase=phase,
            status=status,
            metrics=metrics,
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def add_actions(
        self,
        *,
        research_run_id: UUID,
        round_number: int,
        decisions: tuple[ActionDecision, ...],
        result_by_action: dict[str, dict[str, Any]] | None = None,
    ) -> list[ResearchActionAudit]:
        results = result_by_action or {}
        output: list[ResearchActionAudit] = []
        for decision in decisions:
            result = results.get(decision.action.action_id, {})
            value = ResearchActionAudit(
                research_run_id=research_run_id,
                round_number=round_number,
                action_key=decision.action.action_id,
                action_type=decision.action.action_type,
                query=decision.action.query,
                target_claim_ids=decision.action.target_claim_ids,
                proposal=decision.action.model_dump(mode="json"),
                decision_score=decision.score,
                approved=decision.approved,
                rejection_reason=decision.rejection_reason,
                status=(
                    "succeeded"
                    if decision.approved and not result.get("error")
                    else "rejected"
                    if not decision.approved
                    else "failed"
                ),
                result=result,
                error=str(result.get("error")) if result.get("error") else None,
            )
            self._session.add(value)
            output.append(value)
        await self._session.flush()
        return output

    async def add_claims(
        self,
        *,
        research_run_id: UUID,
        claims: list[InvestigationClaim],
    ) -> list[ResearchClaimTarget]:
        output: list[ResearchClaimTarget] = []
        existing = set(
            (
                await self._session.scalars(
                    select(ResearchClaimTarget.claim_key).where(
                        ResearchClaimTarget.research_run_id == research_run_id
                    )
                )
            ).all()
        )
        for claim in claims:
            if claim.claim_id in existing:
                value = await self._session.scalar(
                    select(ResearchClaimTarget).where(
                        ResearchClaimTarget.research_run_id == research_run_id,
                        ResearchClaimTarget.claim_key == claim.claim_id,
                    )
                )
                if value is not None:
                    value.statement = claim.statement
                    value.claim_type = claim.claim_type
                    value.priority = claim.priority
                    value.falsifiable_prediction = claim.falsifiable_prediction
                    value.required_evidence_types = claim.required_evidence_types
                    value.status = claim.status
                    value.evidence_ids = claim.evidence_ids
                    value.contradicting_evidence_ids = claim.contradicting_evidence_ids
                    value.unresolved_reason = claim.unresolved_reason
                continue
            value = ResearchClaimTarget(
                research_run_id=research_run_id,
                claim_key=claim.claim_id,
                statement=claim.statement,
                claim_type=claim.claim_type,
                priority=claim.priority,
                falsifiable_prediction=claim.falsifiable_prediction,
                required_evidence_types=claim.required_evidence_types,
                status=claim.status,
                evidence_ids=claim.evidence_ids,
                contradicting_evidence_ids=claim.contradicting_evidence_ids,
                unresolved_reason=claim.unresolved_reason,
            )
            self._session.add(value)
            output.append(value)
        await self._session.flush()
        return output

    async def complete_round(
        self,
        *,
        research_run_id: UUID,
        round_number: int,
        verdict: SufficiencyVerdict,
    ) -> None:
        value = await self._session.scalar(
            select(ResearchRound).where(
                ResearchRound.research_run_id == research_run_id,
                ResearchRound.round_number == round_number,
            )
        )
        if value is not None:
            value.status = "completed"
            value.metrics = {
                **value.metrics,
                "sufficient": verdict.sufficient,
                "stop_reason": verdict.stop_reason,
            }
            await self._session.flush()

    async def apply_verified_result(
        self,
        *,
        research_run_id: UUID,
        result: ResearchResult,
    ) -> None:
        targets = list(
            (
                await self._session.scalars(
                    select(ResearchClaimTarget).where(
                        ResearchClaimTarget.research_run_id == research_run_id
                    )
                )
            ).all()
        )
        by_statement = {value.statement.casefold(): value for value in targets}
        for claim in result.claims:
            value = by_statement.get(claim.statement.casefold())
            if value is None:
                value = ResearchClaimTarget(
                    research_run_id=research_run_id,
                    claim_key=f"verified-{len(targets) + 1}",
                    statement=claim.statement,
                    claim_type="verified_evidence_claim",
                    priority=1.0,
                    falsifiable_prediction=claim.statement,
                    required_evidence_types=["verified_workflow_evidence"],
                    status="supported",
                    evidence_ids=[item.evidence_id for item in claim.evidence],
                    contradicting_evidence_ids=[],
                    unresolved_reason=None,
                )
                self._session.add(value)
                targets.append(value)
            value.status = "supported"
            value.evidence_ids = [item.evidence_id for item in claim.evidence]
            value.verification_label = claim.verification_label
            value.verification_confidence = claim.verification_confidence
            value.verification_model = claim.verification_model
            value.verification_version = claim.verification_version
            value.verification_checks = claim.verification_checks
        await self._session.flush()

    async def add_sufficiency(
        self,
        *,
        research_run_id: UUID,
        round_number: int,
        verdict: SufficiencyVerdict,
    ) -> SufficiencyAssessment:
        value = SufficiencyAssessment(
            research_run_id=research_run_id,
            round_number=round_number,
            verdict_data=verdict.model_dump(mode="json"),
            sufficient=verdict.sufficient,
            stop_reason=verdict.stop_reason,
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def append_step(
        self,
        *,
        project_id: UUID,
        workflow_id: UUID,
        round_number: int,
        step_type: str,
        input_summary: str,
        decision: str,
        rationale: str,
        research_run_id: UUID | None = None,
        brainstorm_session_id: UUID | None = None,
        proposed_actions: list[dict[str, Any]] | None = None,
        executed_actions: list[dict[str, Any]] | None = None,
        evidence_ids: list[str] | None = None,
        new_claims: list[str] | None = None,
        resolved_claims: list[str] | None = None,
        unresolved_claims: list[str] | None = None,
        supersedes_step_id: UUID | None = None,
        status: str = "completed",
    ) -> StepRecord:
        step_number = 1 + int(
            (
                await self._session.scalar(
                    select(func.coalesce(func.max(ResearchStepMemory.step_number), 0)).where(
                        ResearchStepMemory.workflow_id == workflow_id
                    )
                )
            )
            or 0
        )
        value = ResearchStepMemory(
            project_id=project_id,
            research_run_id=research_run_id,
            brainstorm_session_id=brainstorm_session_id,
            workflow_id=workflow_id,
            step_number=step_number,
            round_number=round_number,
            step_type=step_type,
            input_summary=input_summary[:4000],
            decision=decision,
            rationale=rationale,
            proposed_actions=proposed_actions or [],
            executed_actions=executed_actions or [],
            evidence_ids=evidence_ids or [],
            new_claims=new_claims or [],
            resolved_claims=resolved_claims or [],
            unresolved_claims=unresolved_claims or [],
            supersedes_step_id=supersedes_step_id,
            status=status,
        )
        self._session.add(value)
        await self._session.flush()
        return StepRecord(value.id, step_number)

    @staticmethod
    def decisions_payload(decisions: tuple[ActionDecision, ...]) -> list[dict[str, Any]]:
        return [action_payload(value) for value in decisions]

    @staticmethod
    def summary(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:4000]
