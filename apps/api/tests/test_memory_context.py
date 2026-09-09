from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.infrastructure.models import (
    Base,
    Project,
    ProjectFact,
    ResearchClaimTarget,
    ResearchRun,
)
from science_buddy.services import memory_context as memory_context_module
from science_buddy.services.memory_context import MemoryContextService
from science_buddy.services.research_audit import ResearchAuditService
from science_buddy.services.route_memory import (
    RetrievalRouteMemoryService,
    assess_retry,
)


class FakeEmbeddingService:
    model_name = "fake-memory-context-e5"
    dimension = 4
    batch_size = 32

    @staticmethod
    def _vector(value: str) -> list[float]:
        lowered = value.casefold()
        raw = [
            float("braf" in lowered or "mapk" in lowered),
            float("resistance" in lowered or "耐药" in lowered),
            float("timeout" in lowered or "检索" in lowered),
            0.1,
        ]
        norm = sum(item * item for item in raw) ** 0.5
        return [item / norm for item in raw]

    async def embed_passages(self, values):  # type: ignore[no-untyped-def]
        return [self._vector(value) for value in values]

    async def embed_queries(self, values):  # type: ignore[no-untyped-def]
        return [self._vector(value) for value in values]


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_memory_context_enforces_consumer_policy_and_evidence_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await make_database(tmp_path / "memory-context.db")
    monkeypatch.setattr(
        memory_context_module,
        "get_embedding_service",
        lambda: FakeEmbeddingService(),
    )
    async with sessions() as session:
        project = Project(name="Unified memory project")
        session.add(project)
        await session.flush()
        run = ResearchRun(
            project_id=project.id,
            question="BRAF MAPK resistance",
            language="en",
            status="verified",
            retrieval_mode="hybrid-dense",
            model_provider="test",
            model_name="test",
            retrieval_config={},
            retrieval_trace={},
            result={},
        )
        session.add(run)
        await session.flush()
        fact = ProjectFact(
            project_id=project.id,
            category="verified_research_claim",
            statement="BRAF MAPK signaling contributes to treatment resistance.",
            statement_hash="a" * 64,
            source_type="research_run",
            source_id=run.id,
            source_locator={"evidence_ids": ["ev1.historical-fact"]},
            confidence=0.95,
            importance=0.95,
            status="active",
        )
        claim = ResearchClaimTarget(
            research_run_id=run.id,
            claim_key="verified-1",
            statement="BRAF MAPK signaling is associated with treatment resistance.",
            claim_type="verified_evidence_claim",
            priority=1.0,
            falsifiable_prediction="Resistance changes after pathway perturbation.",
            required_evidence_types=["verified_workflow_evidence"],
            status="supported",
            evidence_ids=["ev1.historical-claim"],
            contradicting_evidence_ids=[],
            verification_label="entailment",
            verification_confidence=0.94,
            verification_model="test-nli",
            verification_version="claim-nli-v1",
            verification_checks={"effect_direction": "pass"},
        )
        session.add_all([fact, claim])
        await session.flush()
        await ResearchAuditService(session).append_step(
            project_id=project.id,
            workflow_id=uuid4(),
            round_number=1,
            step_type="retrieve",
            input_summary="BRAF resistance search experience",
            decision="continue",
            rationale="The previous route returned useful candidates.",
            research_run_id=run.id,
            evidence_ids=["ev1.historical-step"],
        )
        route = await RetrievalRouteMemoryService(session).record(
            project_id=project.id,
            workflow_id=uuid4(),
            workflow_kind="research",
            research_run_id=run.id,
            query="BRAF resistance timeout query",
            tool="dense_original",
            scope={"project_id": str(project.id), "collection_id": None},
            result_count=0,
            status="failed",
            failure_reason="route timeout",
        )
        await session.flush()

        brainstorm = await MemoryContextService(session).recall(
            project_id=project.id,
            query="BRAF MAPK resistance timeout",
            consumer="brainstorm",
            statuses=("active",),
            limit=10,
        )
        synthesis = await MemoryContextService(session).recall(
            project_id=project.id,
            query="BRAF MAPK resistance timeout",
            consumer="synthesis",
            statuses=("active",),
            limit=10,
        )
        with pytest.raises(ValueError, match="not allowed for brainstorm"):
            await MemoryContextService(session).recall(
                project_id=project.id,
                query="BRAF",
                consumer="brainstorm",
                memory_types=("verified_claim",),
            )

    assert "verified_claim" not in brainstorm.allowed_memory_types
    assert {value.memory_type for value in synthesis.items} == {
        "project_fact",
        "research_step",
        "verified_claim",
        "failed_route",
    }
    assert synthesis.items[0].role == "confirmed_project_fact"
    assert all(value.evidence_eligible is False for value in synthesis.items)
    step = next(value for value in synthesis.items if value.memory_type == "research_step")
    assert "evidence_ids" not in step.source
    assert step.source["historical_evidence_count"] == 1
    claim_item = next(
        value for value in synthesis.items if value.memory_type == "verified_claim"
    )
    assert claim_item.source["historical_evidence_ids"] == ["ev1.historical-claim"]
    assert claim_item.source["citation_requires_current_retrieval"] is True
    fact_item = next(
        value for value in synthesis.items if value.memory_type == "project_fact"
    )
    locator = fact_item.source["source_locator"]
    assert "evidence_ids" not in locator
    assert locator["historical_evidence_ids"] == ["ev1.historical-fact"]
    assert route.query == "BRAF resistance timeout query"
    assert route.tool == "dense_original"
    assert route.scope["project_id"] == str(project.id)
    assert route.result_count == 0
    assert route.failure_reason == "route timeout"
    assert route.retry_worthy is True
    assert route.retry_reason == "transient_failure_retry_with_backoff"
    await engine.dispose()  # type: ignore[attr-defined]


def test_route_retry_assessment_is_structured_and_conservative() -> None:
    transient = assess_retry("HTTP 503 timeout", status="failed")
    configuration = assess_retry("web_search_not_configured", status="failed")
    empty = assess_retry("no_candidates", status="empty")

    assert transient.retry_worthy is True
    assert transient.reason == "transient_failure_retry_with_backoff"
    assert configuration.retry_worthy is False
    assert configuration.reason == "change_permissions_configuration_or_action"
    assert empty.retry_worthy is False
    assert empty.reason == "reformulate_query_or_change_scope"
