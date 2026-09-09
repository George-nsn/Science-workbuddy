from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.infrastructure.models import (
    Base,
    MemoryDerivedIndex,
    Project,
    ProjectFact,
    ProjectFactRelation,
    ResearchRun,
)
from science_buddy.services.memory_retrieval import (
    HybridMemoryRetrievalService,
    MemoryRecallFilters,
)
from science_buddy.services.research_audit import ResearchAuditService


class FakeEmbeddingService:
    model_name = "fake-e5"
    dimension = 4
    batch_size = 16

    def __init__(self) -> None:
        self.passage_calls = 0

    def _vector(self, value: str) -> list[float]:
        lowered = value.casefold()
        raw = [
            float("braf" in lowered or "mapk" in lowered),
            float("resistance" in lowered or "耐药" in lowered),
            float("safety" in lowered or "安全" in lowered),
            0.1,
        ]
        norm = sum(item * item for item in raw) ** 0.5
        return [item / norm for item in raw]

    async def embed_passages(self, values):  # type: ignore[no-untyped-def]
        self.passage_calls += 1
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
async def test_hybrid_memory_recall_fuses_routes_mmr_and_related_facts(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "memory-recall.db")
    async with sessions() as session:
        project = Project(name="Recall project")
        session.add(project)
        await session.flush()
        supported = ProjectFact(
            project_id=project.id,
            category="verified_research_claim",
            statement="BRAF MAPK signaling is associated with treatment resistance.",
            statement_hash="a" * 64,
            source_type="research_run",
            source_id=uuid4(),
            source_locator={"evidence_ids": ["ev1.test"]},
            confidence=0.95,
            importance=0.95,
            status="active",
        )
        conflict = ProjectFact(
            project_id=project.id,
            category="verified_research_claim",
            statement="BRAF MAPK signaling is not associated with treatment resistance.",
            statement_hash="b" * 64,
            source_type="research_run",
            source_id=uuid4(),
            source_locator={},
            confidence=0.8,
            importance=0.85,
            status="superseded",
        )
        session.add_all([supported, conflict])
        await session.flush()
        session.add(
            ProjectFactRelation(
                project_id=project.id,
                source_fact_id=supported.id,
                target_fact_id=conflict.id,
                relation_type="conflicts_with",
                provenance={"test": True},
            )
        )
        run = ResearchRun(
            project_id=project.id,
            question="BRAF resistance",
            language="en",
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
        await ResearchAuditService(session).append_step(
            project_id=project.id,
            workflow_id=uuid4(),
            round_number=1,
            step_type="verify",
            input_summary="BRAF MAPK resistance evidence verified",
            decision="stop",
            rationale="sufficient",
            research_run_id=run.id,
            evidence_ids=["ev1.test"],
        )
        service = HybridMemoryRetrievalService(
            session,
            embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
        )
        result = await service.recall(
            project_id=project.id,
            query="BRAF treatment resistance",
            filters=MemoryRecallFilters(statuses=("active", "superseded")),
            limit=3,
        )
        indexed_again = await service.refresh_project(project.id)

    assert result.items
    assert result.items[0].dense_score > 0
    assert "semantic" in result.items[0].routes
    fact_item = next(
        value for value in result.items if value.entity_type == "project_fact"
    )
    assert any(value["relation"] == "conflicts_with" for value in fact_item.related)
    assert indexed_again == 0
    await engine.dispose()  # type: ignore[attr-defined]


def _add_fact(session: object, project: Project) -> ProjectFact:
    fact = ProjectFact(
        project_id=project.id,
        category="research_context",
        statement="BRAF MAPK signaling is associated with treatment resistance.",
        statement_hash="f" * 64,
        source_type="brainstorm",
        source_id=uuid4(),
        source_locator={},
        confidence=0.9,
        importance=0.8,
        status="active",
    )
    session.add(fact)  # type: ignore[attr-defined]
    return fact


@pytest.mark.asyncio
async def test_refresh_fast_path_skips_rescan_and_reembedding(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "memory-fastpath.db")
    embedding = FakeEmbeddingService()
    async with sessions() as session:
        project = Project(name="Fast path project")
        session.add(project)
        await session.flush()
        fact = _add_fact(session, project)
        await session.flush()
        # Move the fact timestamp into the past so the index marker is newer.
        await session.execute(
            update(ProjectFact)
            .where(ProjectFact.id == fact.id)
            .values(updated_at=datetime.now(UTC) - timedelta(seconds=10))
        )
        service = HybridMemoryRetrievalService(
            session, embedding_service=embedding  # type: ignore[arg-type]
        )
        first = await service.refresh_project(project.id)
        calls_after_first = embedding.passage_calls
        second = await service.refresh_project(project.id)
        third = await service.refresh_project(project.id)

    assert first == 1
    assert calls_after_first == 1
    assert second == 0
    assert third == 0
    assert embedding.passage_calls == 1  # no further embedding on fast path
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_fact_statement_change_triggers_reindex(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "memory-reindex.db")
    embedding = FakeEmbeddingService()
    async with sessions() as session:
        project = Project(name="Reindex project")
        session.add(project)
        await session.flush()
        fact = _add_fact(session, project)
        await session.flush()
        await session.execute(
            update(ProjectFact)
            .where(ProjectFact.id == fact.id)
            .values(updated_at=datetime.now(UTC) - timedelta(seconds=10))
        )
        service = HybridMemoryRetrievalService(
            session, embedding_service=embedding  # type: ignore[arg-type]
        )
        assert await service.refresh_project(project.id) == 1
        fact.statement = "BRAF MAPK signaling drives resistance via feedback loops."
        await session.flush()
        refreshed = await service.refresh_project(project.id)
        indexed_row = await session.scalar(
            select(MemoryDerivedIndex).where(
                MemoryDerivedIndex.entity_id == fact.id,
                MemoryDerivedIndex.entity_type == "project_fact",
            )
        )

    assert refreshed == 1
    assert indexed_row is not None
    assert indexed_row.search_text == fact.statement
    assert embedding.passage_calls == 2
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_soft_deleted_fact_is_removed_from_index_and_recall(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "memory-delete.db")
    embedding = FakeEmbeddingService()
    async with sessions() as session:
        project = Project(name="Delete project")
        session.add(project)
        await session.flush()
        fact = _add_fact(session, project)
        await session.flush()
        await session.execute(
            update(ProjectFact)
            .where(ProjectFact.id == fact.id)
            .values(updated_at=datetime.now(UTC) - timedelta(seconds=10))
        )
        service = HybridMemoryRetrievalService(
            session, embedding_service=embedding  # type: ignore[arg-type]
        )
        assert await service.refresh_project(project.id) == 1
        fact.deleted_at = datetime.now(UTC)
        await session.flush()
        refreshed = await service.refresh_project(project.id)
        remaining_rows = list(
            (
                await session.scalars(
                    select(MemoryDerivedIndex).where(
                        MemoryDerivedIndex.project_id == project.id
                    )
                )
            ).all()
        )
        result = await service.recall(
            project_id=project.id,
            query="BRAF treatment resistance",
        )

    assert refreshed == 0
    assert remaining_rows == []
    assert result.items == ()
    assert embedding.passage_calls == 1
    await engine.dispose()  # type: ignore[attr-defined]
