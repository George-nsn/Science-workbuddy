from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.infrastructure.models import (
    Base,
    BrainstormSessionMemory,
    Project,
    ProjectFact,
    ProjectFactRelation,
)
from science_buddy.services.brainstorm_sessions import BrainstormSessionService
from science_buddy.services.project_memory import ProjectMemoryService, extract_fact_drafts


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


def structured_payload() -> dict[str, object]:
    return {
        "question_agent": {
            "scientific_question": "Does marker X predict outcome?",
            "hypothesis": "Marker X is associated with outcome.",
            "predictions": ["The high group has better outcome."],
            "evidence_gaps": ["External validation is missing."],
        },
        "experiment_agent": {
            "objectives": ["Estimate the association."],
            "work_packages": [
                {
                    "title": "Cohort assembly",
                    "purpose": "Define cases and controls",
                    "approach": "Apply blinded inclusion criteria",
                    "evidence_ids": ["ev1.test"],
                }
            ],
            "analysis_plan": ["Use a pre-specified model."],
            "reproducibility_plan": ["Freeze the analysis script."],
            "safety_flags": [],
        },
        "critic_agent": {
            "limitations": ["Residual confounding."],
            "feasibility_risks": ["Small sample size."],
        },
        "coordinator": {"safety_flags": []},
    }


def test_fact_extraction_is_deterministic_and_classified() -> None:
    facts = extract_fact_drafts(structured_payload())
    assert {fact.category for fact in facts} >= {
        "scientific_question",
        "hypothesis",
        "objective",
        "work_package",
        "analysis_plan",
        "reproducibility",
        "limitation",
        "risk",
        "evidence_gap",
    }
    assert len({(fact.category, fact.statement) for fact in facts}) == len(facts)


@pytest.mark.asyncio
async def test_session_summary_and_project_facts_are_idempotent(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "project-memory.db")
    async with sessions() as session:
        project = Project(name="Memory project")
        session.add(project)
        await session.commit()
        service = BrainstormSessionService(session)
        brainstorm = await service.create(
            project_id=project.id,
            mode="exploration",
            title="Marker plan",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )
        message = await service.append_message(
            session_id=brainstorm.id,
            role="assistant",
            agent_name="coordinator",
            content="## Plan",
            payload=structured_payload(),
        )
        memory = ProjectMemoryService(session)
        await memory.record_brainstorm_turn(
            project_id=project.id,
            session_id=brainstorm.id,
            message=message,
            turn_number=1,
            session_title=brainstorm.title,
        )
        await memory.record_brainstorm_turn(
            project_id=project.id,
            session_id=brainstorm.id,
            message=message,
            turn_number=1,
            session_title=brainstorm.title,
        )
        await session.commit()
        summaries = list((await session.scalars(select(BrainstormSessionMemory))).all())
        facts = list((await session.scalars(select(ProjectFact))).all())
        context = await memory.context_pack(project.id, brainstorm.id)

    assert len(summaries) == 1
    assert summaries[0].turn_number == 1
    assert "核心假设" in summaries[0].summary_markdown
    assert len(facts) == len(extract_fact_drafts(structured_payload()))
    assert all(0 <= fact.confidence <= 1 for fact in facts)
    assert all(0 <= fact.importance <= 1 for fact in facts)
    assert context["session_summary"]
    assert context["project_facts"]
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_fact_relations_keep_conflicts_and_apply_explicit_supersede(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "fact-relations.db")
    async with sessions() as session:
        project = Project(name="Fact relation project")
        session.add(project)
        await session.flush()
        first = ProjectFact(
            project_id=project.id,
            category="verified_research_claim",
            statement="Marker X increases risk.",
            statement_hash="a" * 64,
            source_type="research_run",
            source_id=uuid4(),
            source_locator={},
            confidence=0.9,
            importance=0.9,
            status="active",
        )
        second = ProjectFact(
            project_id=project.id,
            category="verified_research_claim",
            statement="Marker X does not increase risk.",
            statement_hash="b" * 64,
            source_type="research_run",
            source_id=uuid4(),
            source_locator={},
            confidence=0.9,
            importance=0.9,
            status="active",
        )
        session.add_all([first, second])
        await session.commit()
        service = ProjectMemoryService(session)
        conflict = await service.relate_facts(
            project_id=project.id,
            source_fact_id=second.id,
            target_fact_id=first.id,
            relation_type="conflicts_with",
        )
        assert first.status == "active"
        supersede = await service.relate_facts(
            project_id=project.id,
            source_fact_id=second.id,
            target_fact_id=first.id,
            relation_type="supersedes",
        )
        relations = list((await session.scalars(select(ProjectFactRelation))).all())

    assert conflict.relation_type == "conflicts_with"
    assert supersede.relation_type == "supersedes"
    assert first.status == "superseded"
    assert len(relations) == 2
    await engine.dispose()  # type: ignore[attr-defined]