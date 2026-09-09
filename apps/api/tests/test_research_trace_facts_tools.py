from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.infrastructure.models import (
    Base,
    Project,
    ProjectFact,
    ResearchRun,
)
from science_buddy.services.dynamic_research import (
    InvestigationClaim,
    ResearchPlan,
    SufficiencyVerdict,
)
from science_buddy.services.research import FinalClaim, FinalEvidence, ResearchResult
from science_buddy.services.research_audit import ResearchAuditService
from science_buddy.services.research_facts import ResearchFactPublisher
from science_buddy.services.research_trace import (
    build_research_trace,
    trace_audit_package,
    traceable_review_markdown,
)
from science_buddy.services.tool_registry import get_enabled_tool, list_tool_registrations


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_trace_review_audit_and_confirmed_fact_publication(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "research-trace.db")
    async with sessions() as session:
        project = Project(name="Trace project")
        session.add(project)
        await session.flush()
        result = ResearchResult(
            answer="Verified answer [ev1.test]",
            claims=[
                FinalClaim(
                    statement="BRAF signaling is associated with resistance.",
                    relation="supports",
                    evidence=[
                        FinalEvidence(
                            evidence_id="ev1.test",
                            text="BRAF resistance evidence.",
                            source_locator={"pmid": "123", "section_path": "Results"},
                            formatted_citation="Author. Study. Journal. 2026.",
                        )
                    ],
                    semantic_verification="Directly supported.",
                    verification_confidence=0.94,
                    verification_model="test-nli",
                )
            ],
            gaps=["Long-term evidence is missing."],
            conflicts=["One subgroup had a null result."],
        )
        run = ResearchRun(
            project_id=project.id,
            question="What drives resistance?",
            language="en",
            status="verified",
            retrieval_mode="hybrid-dense",
            model_provider="test",
            model_name="test",
            retrieval_config={},
            retrieval_trace={},
            result=result.model_dump(mode="json"),
        )
        session.add(run)
        await session.flush()
        audit = ResearchAuditService(session)
        base = ResearchPlan(
            question_type="mechanism",
            strategy="direct",
            core_claims=[
                InvestigationClaim(
                    claim_id="core",
                    statement="What drives resistance?",
                )
            ],
            required_subqueries=["BRAF resistance"],
        )
        first = await audit.add_plan(
            research_run_id=run.id,
            plan=base,
            source="deterministic",
        )
        expanded = base.model_copy(
            update={
                "core_claims": [
                    *base.core_claims,
                    InvestigationClaim(
                        claim_id="alternative",
                        statement="Alternative mechanism",
                    ),
                ],
                "exploratory_subqueries": ["alternative mechanism"],
            }
        )
        await audit.add_plan(
            research_run_id=run.id,
            plan=expanded,
            source="model",
            parent_snapshot_id=first.id,
        )
        await audit.add_round(
            research_run_id=run.id,
            round_number=1,
            phase="plan",
            status="completed",
            metrics={},
        )
        await audit.add_claims(research_run_id=run.id, claims=expanded.core_claims)
        await audit.apply_verified_result(research_run_id=run.id, result=result)
        await audit.add_sufficiency(
            research_run_id=run.id,
            round_number=1,
            verdict=SufficiencyVerdict(
                sufficient=True,
                stop_reason="core_claims_and_counterevidence_covered",
            ),
        )
        trail = await audit.trail(run.id)
        trace = build_research_trace(trail)
        review, cited = traceable_review_markdown(run)
        package = trace_audit_package(run, trail)
        published = await ResearchFactPublisher(session).publish(
            run=run,
            published_by="user",
        )
        second = await ResearchFactPublisher(session).publish(
            run=run,
            published_by="user",
        )
        await session.commit()
        facts = list(
            (
                await session.scalars(
                    select(ProjectFact).where(ProjectFact.source_id == run.id)
                )
            ).all()
        )

    assert trace.plans[1].added_claim_ids == ("alternative",)
    assert trace.claims[-1].verification_label == "entailment"
    assert "## 冲突" in review and "[ev1.test]" in review
    assert cited == 1
    assert package["schema_version"] == "science-buddy-research-audit-v1"
    assert published.created == 1 and len(facts) == 1
    assert second.reused == 1
    await engine.dispose()  # type: ignore[attr-defined]


def test_tool_registry_keeps_external_tools_disabled_until_verified() -> None:
    tools = {value.tool_id: value for value in list_tool_registrations()}

    assert get_enabled_tool("local_hybrid_search").evidence_eligible
    assert not get_enabled_tool("workbench_plot_agent").evidence_eligible
    assert tools["clinicaltrials_gov"].mechanical_verification.startswith("NCT")
    assert tools["generic_mcp"].implementation_status == "registered_only"
    with pytest.raises(PermissionError):
        get_enabled_tool("blast")


def test_action_allowlist_derives_from_tool_registry_single_source() -> None:
    from typing import get_args

    from science_buddy.services.dynamic_research import (
        _ALLOWED_ACTION_TYPES,
        ActionType,
    )
    from science_buddy.services.tool_registry import RESEARCH_ACTION_TOOL_IDS

    # Every registry research-action tool is an allowed action, and only the
    # control-flow stop signal exists outside the registry. Tools with their
    # own execution surfaces (e.g. workbench_plot_agent) are not actions.
    assert _ALLOWED_ACTION_TYPES - {"stop_research"} == RESEARCH_ACTION_TOOL_IDS
    assert "workbench_plot_agent" not in _ALLOWED_ACTION_TYPES
    # The Pydantic Literal stays in sync with the runtime whitelist.
    assert set(get_args(ActionType)) == _ALLOWED_ACTION_TYPES


def test_deterministic_research_plan_uses_registry_derived_allowlist() -> None:
    from science_buddy.services.dynamic_research import (
        ResearchBudget,
        ResearchPermissions,
        deterministic_research_plan,
    )
    from science_buddy.services.research_routing import (
        DeterministicResearchRouter,
    )

    decision = DeterministicResearchRouter().route(
        "甲状腺癌免疫治疗的疗效与安全性",
        preference="direct",
    )
    plan = deterministic_research_plan(
        "甲状腺癌免疫治疗的疗效与安全性",
        decision,
        budget=ResearchBudget.for_depth("balanced"),
        permissions=ResearchPermissions(
            allow_web_search=False,
            allow_scholarly_discovery=False,
            allow_auto_import=False,
        ),
    )

    assert "local_hybrid_search" in plan.allowed_actions
    assert "graph_local_search" in plan.allowed_actions
    assert "stop_research" in plan.allowed_actions
    assert "scholarly_discovery" not in plan.allowed_actions
    assert "controlled_web_search" not in plan.allowed_actions
