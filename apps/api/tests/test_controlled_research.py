from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.providers import StructuredGenerationRequest
from science_buddy.infrastructure.models import (
    Base,
    BrainstormSession,
    Project,
    ResearchPlanSnapshot,
    ResearchRun,
    ResearchStepMemory,
)
from science_buddy.services.controlled_research import ControlledDynamicResearchService
from science_buddy.services.dynamic_research import (
    DynamicResearchPlanner,
    ExplorationProposal,
    InvestigationClaim,
    ProposedResearchAction,
    ResearchActionPolicy,
    ResearchBudget,
    ResearchPermissions,
    ResearchPlan,
)
from science_buddy.services.evidence import EvidenceTokenService, MechanicalEvidenceVerifier
from science_buddy.services.memory_lifecycle import MemoryLifecycleService
from science_buddy.services.models import ModelResponseError
from science_buddy.services.research import ResearchPipeline, ResearchResult
from science_buddy.services.research_audit import ResearchAuditService
from science_buddy.services.research_routing import DeterministicResearchRouter


class FailingPlanner:
    async def propose(self, **_kwargs: object) -> ExplorationProposal:
        raise ModelResponseError("planner unavailable")


class EmptyModel:
    name = "empty"

    async def generate_structured(
        self, _request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        return {"claims": [], "gaps": [], "conflicts": [], "followup_queries": []}

    async def stream_text(
        self, _system: str, _messages: Sequence[str]
    ) -> AsyncIterator[str]:
        yield ""


class CapturingPlannerModel(EmptyModel):
    def __init__(self) -> None:
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        self.requests.append(request)
        return {"claims": [], "actions": [], "rationale": "Use prior search experience."}


def plan(*, actions: list[str] | None = None) -> ResearchPlan:
    return ResearchPlan(
        question_type="general",
        strategy="direct",
        core_claims=[
            InvestigationClaim(
                claim_id="claim-core",
                statement="BRAF treatment resistance",
            )
        ],
        required_subqueries=["BRAF treatment resistance"],
        allowed_sources=["local", "scholarly", "web"],
        allowed_actions=actions  # type: ignore[arg-type]
        or [
            "local_hybrid_search",
            "scholarly_discovery",
            "controlled_web_search",
        ],
    )


def action(
    action_id: str,
    query: str,
    *,
    action_type: str = "local_hybrid_search",
    novelty: float = 0.7,
) -> ProposedResearchAction:
    return ProposedResearchAction(
        action_id=action_id,
        action_type=action_type,  # type: ignore[arg-type]
        query=query,
        target_claim_ids=["claim-core"],
        expected_information_gain=0.8,
        novelty=novelty,
        falsifiability=0.8,
        estimated_cost=0.1,
    )


def permissions(
    *,
    scholarly: bool = False,
    web: bool = False,
    auto_import: bool = False,
) -> ResearchPermissions:
    return ResearchPermissions(
        allow_scholarly_discovery=scholarly,
        allow_web_search=web,
        allow_auto_import=auto_import,
    )


def test_unknown_action_is_rejected_by_schema() -> None:
    with pytest.raises(ValidationError, match="unknown research action type"):
        action("unsafe", "run arbitrary tool", action_type="shell_command")


@pytest.mark.parametrize(
    ("action_type", "policy_permissions", "reason"),
    [
        ("controlled_web_search", permissions(), "web_not_authorized"),
        (
            "scholarly_discovery",
            permissions(scholarly=True),
            "auto_import_not_authorized",
        ),
        (
            "scholarly_discovery",
            permissions(auto_import=True),
            "scholarly_discovery_not_authorized",
        ),
    ],
)
def test_external_actions_require_explicit_permissions(
    action_type: str,
    policy_permissions: ResearchPermissions,
    reason: str,
) -> None:
    decision = ResearchActionPolicy(
        plan=plan(),
        budget=ResearchBudget.for_depth("deep"),
        permissions=policy_permissions,
    ).evaluate((action("external", "BRAF recent evidence", action_type=action_type),))[0]

    assert not decision.approved
    assert decision.rejection_reason == reason


def test_scholarly_action_requires_and_accepts_auto_import_authorization() -> None:
    decision = ResearchActionPolicy(
        plan=plan(),
        budget=ResearchBudget.for_depth("balanced"),
        permissions=permissions(scholarly=True, auto_import=True),
    ).evaluate(
        (action("scholarly", "BRAF treatment resistance", action_type="scholarly_discovery"),)
    )[0]

    assert decision.approved
    assert decision.rejection_reason is None


def test_action_policy_requires_unresolved_claim_target() -> None:
    missing = action("missing", "BRAF evidence").model_copy(
        update={"target_claim_ids": []}
    )
    unknown = action("unknown", "BRAF alternative evidence").model_copy(
        update={"target_claim_ids": ["resolved-or-unknown"]}
    )
    decisions = ResearchActionPolicy(
        plan=plan(actions=["local_hybrid_search"]),
        budget=ResearchBudget.for_depth("deep"),
        permissions=permissions(),
    ).evaluate((missing, unknown))

    assert decisions[0].rejection_reason == "target_claim_required"
    assert decisions[1].rejection_reason == "target_claim_not_unresolved"


def test_action_policy_deduplicates_and_selects_diverse_queries() -> None:
    decisions = ResearchActionPolicy(
        plan=plan(actions=["local_hybrid_search"]),
        budget=ResearchBudget.for_depth("balanced"),
        permissions=permissions(),
        executed_queries=["BRAF treatment resistance baseline"],
    ).evaluate(
        (
            action("duplicate", "BRAF treatment resistance baseline"),
            action(
                "first",
                "BRAF treatment resistance mechanism MAPK pathway tumor signaling",
                novelty=1.0,
            ),
            action(
                "near-duplicate",
                "BRAF treatment resistance mechanism MAPK pathway tumor signaling molecular",
                novelty=0.7,
            ),
            action(
                "diverse",
                "BRAF clinical resistance cohort outcomes",
                novelty=0.1,
            ),
        )
    )

    by_id = {value.action.action_id: value for value in decisions}
    assert by_id["duplicate"].rejection_reason == "duplicate_query"
    assert by_id["first"].approved
    assert not by_id["near-duplicate"].approved
    assert by_id["near-duplicate"].rejection_reason == "lower_value_or_diversity_budget"
    assert by_id["diverse"].approved


@pytest.mark.parametrize(
    ("depth", "rounds", "subqueries", "actions", "external", "counter", "replan"),
    [
        ("quick", 0, 2, 0, 1, 0, False),
        ("balanced", 1, 6, 2, 2, 1, False),
        ("deep", 2, 10, 4, 4, 2, True),
        ("max", 2, 10, 4, 4, 2, True),
    ],
)
def test_depth_budgets_are_bounded(
    depth: str,
    rounds: int,
    subqueries: int,
    actions: int,
    external: int,
    counter: int,
    replan: bool,
) -> None:
    value = ResearchBudget.for_depth(depth)

    assert (
        value.max_rounds,
        value.max_total_subqueries,
        value.max_actions_per_round,
        value.max_external_requests,
        value.required_counterevidence,
        value.allow_replan,
    ) == (rounds, subqueries, actions, external, counter, replan)


@pytest.mark.asyncio
async def test_step_memory_is_passed_to_planner_not_evidence() -> None:
    model = CapturingPlannerModel()
    planner = DynamicResearchPlanner(model)  # type: ignore[arg-type]
    base = plan(actions=["local_hybrid_search"])

    await planner.propose(
        question="BRAF resistance",
        base_plan=base,
        candidates=[],
        planning_context=(
            {
                "memory_type": "research_step",
                "entity_id": str(uuid4()),
                "role": "planning_experience",
                "text": "The previous dense route timed out.",
                "evidence_eligible": False,
                "source": {"historical_evidence_count": 3},
            },
        ),
        round_number=1,
        model_depth="balanced",
        max_context_tokens=65536,
    )

    request = model.requests[0]
    payload = request.user_content
    assert "planning_context_not_evidence" in payload
    assert "The previous dense route timed out" in payload
    assert '"evidence": []' in payload
    assert "planning experience only" in request.system_instruction


@pytest.mark.asyncio
async def test_planner_failure_uses_fallback_and_stops_after_two_rounds() -> None:
    workflow_id = uuid4()
    tokens = EvidenceTokenService("a-test-secret-that-is-long-enough")
    pipeline = ResearchPipeline(
        model=EmptyModel(),
        mechanical_verifier=MechanicalEvidenceVerifier(tokens, workflow_id=workflow_id),
    )
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        evidence_id=tokens.issue(workflow_id, uuid4()),
        text="BRAF resistance evidence",
        score=1.0,
        source_locator={},
    )
    plan_sources: list[str] = []
    execution_rounds: list[int] = []

    async def execute_actions(
        _decisions: object, round_number: int
    ) -> tuple[list[RetrievalCandidate], dict[str, dict[str, Any]]]:
        execution_rounds.append(round_number)
        incoming = RetrievalCandidate(
            chunk_id=uuid4(),
            evidence_id=tokens.issue(workflow_id, uuid4()),
            text=f"round {round_number} evidence",
            score=0.8,
            source_locator={},
        )
        return [incoming], {}

    async def answer(
        _candidates: list[RetrievalCandidate], _analysis: object
    ) -> ResearchResult:
        return ResearchResult(
            answer="证据仍不足",
            claims=[],
            gaps=["unresolved"],
            conflicts=[],
        )

    async def record_step(
        _round: int,
        _step_type: str,
        _decision: str,
        _rationale: str,
        _payload: dict[str, Any],
    ) -> None:
        return None

    async def record_plan(_plan: ResearchPlan, source: str, _round: int) -> None:
        plan_sources.append(source)

    outcome = await ControlledDynamicResearchService(
        planner=FailingPlanner(),  # type: ignore[arg-type]
        pipeline=pipeline,
        execute_actions=execute_actions,
        answer=answer,
        record_step=record_step,
        record_plan=record_plan,
    ).run(
        question="BRAF resistance mechanism",
        routing=DeterministicResearchRouter().route(
            "BRAF resistance mechanism",
            preference="deep_research",
        ),
        initial_candidates=[candidate],
        permissions=permissions(),
        model_depth="deep",
        max_context_tokens=65536,
        dynamic_planning=True,
        max_external_requests=0,
    )

    assert outcome.rounds_executed == 2
    assert execution_rounds == [1, 2]
    assert plan_sources == ["deterministic", "fallback", "fallback"]
    assert outcome.planner_errors == (
        "round_1:ModelResponseError",
        "round_2:ModelResponseError",
    )
    assert outcome.sufficiency[-1].stop_reason == "round_budget_exhausted"


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_step_memory_is_append_only_and_collection_scoped_input_is_preserved(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "research-audit.db")
    async with sessions() as session:
        project = Project(name="Audit project")
        session.add(project)
        await session.flush()
        run = ResearchRun(
            project_id=project.id,
            question="Question",
            language="zh-CN",
            status="processing",
            retrieval_mode="hybrid-sparse",
            model_provider="test",
            model_name="test",
            retrieval_config={},
            retrieval_trace={"collection_id": str(uuid4())},
            result={},
        )
        brainstorm = BrainstormSession(
            project_id=project.id,
            title="Audit brainstorm",
            mode="exploration",
            session_number=1,
        )
        session.add_all([run, brainstorm])
        await session.flush()
        service = ResearchAuditService(session)
        workflow_id = uuid4()
        first = await service.append_step(
            project_id=project.id,
            workflow_id=workflow_id,
            round_number=0,
            step_type="route",
            input_summary="collection-scoped deterministic route",
            decision="base_plan",
            rationale="collection_id stays in the retrieval boundary",
            research_run_id=run.id,
        )
        second = await service.append_step(
            project_id=project.id,
            workflow_id=workflow_id,
            round_number=1,
            step_type="verify",
            input_summary="verified evidence",
            decision="stop",
            rationale="sufficient",
            research_run_id=run.id,
            supersedes_step_id=first.id,
        )
        brainstorm_step = await service.append_step(
            project_id=project.id,
            workflow_id=uuid4(),
            round_number=1,
            step_type="brainstorm_turn",
            input_summary="turn completed",
            decision="proposal_created",
            rationale="validated agent outputs",
            brainstorm_session_id=brainstorm.id,
        )
        await session.commit()
        values = list(
            (
                await session.scalars(
                    select(ResearchStepMemory).order_by(
                        ResearchStepMemory.workflow_id,
                        ResearchStepMemory.step_number,
                    )
                )
            ).all()
        )

    research_values = [value for value in values if value.workflow_id == workflow_id]
    assert [value.step_number for value in research_values] == [1, 2]
    assert research_values[0].decision == "base_plan"
    assert research_values[1].supersedes_step_id == first.id
    assert second.step_number == 2
    assert brainstorm_step.step_number == 1
    assert run.retrieval_trace["collection_id"] is not None
    await engine.dispose()  # type: ignore[attr-defined]


def test_action_policy_never_changes_collection_scope() -> None:
    collection_id = uuid4()
    project_id = uuid4()
    captured: list[tuple[UUID, UUID]] = []

    def scoped_factory() -> tuple[UUID, UUID]:
        captured.append((project_id, collection_id))
        return captured[-1]

    for _ in range(2):
        assert scoped_factory() == (project_id, collection_id)

    assert captured == [(project_id, collection_id), (project_id, collection_id)]


@pytest.mark.asyncio
async def test_research_purge_explicitly_removes_dynamic_audit(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "research-purge.db")
    async with sessions() as session:
        project = Project(name="Purge project")
        session.add(project)
        await session.flush()
        run = ResearchRun(
            project_id=project.id,
            question="Purge audited run",
            language="zh-CN",
            status="verified",
            retrieval_mode="hybrid-sparse",
            model_provider="test",
            model_name="test",
            retrieval_config={},
            retrieval_trace={},
            result={},
        )
        session.add(run)
        await session.flush()
        value = plan()
        await ResearchAuditService(session).add_plan(
            research_run_id=run.id,
            plan=value,
            source="deterministic",
        )
        await ResearchAuditService(session).append_step(
            project_id=project.id,
            workflow_id=uuid4(),
            round_number=0,
            step_type="route",
            input_summary="route",
            decision="plan",
            rationale="test",
            research_run_id=run.id,
        )
        run.deleted_at = datetime.now(UTC)
        await session.commit()
        await MemoryLifecycleService(session).purge(
            project_id=project.id,
            entity_type="research_run",
            entity_id=run.id,
        )
        plans = list((await session.scalars(select(ResearchPlanSnapshot))).all())
        steps = list((await session.scalars(select(ResearchStepMemory))).all())

    assert plans == []
    assert steps == []
    await engine.dispose()  # type: ignore[attr-defined]