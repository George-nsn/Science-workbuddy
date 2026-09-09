from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import research as research_api
from science_buddy.config import Settings
from science_buddy.domain.providers import StructuredGenerationRequest
from science_buddy.infrastructure.models import (
    Base,
    Project,
    ResearchPlanSnapshot,
    ResearchRun,
    ResearchStepMemory,
    SufficiencyAssessment,
)
from science_buddy.main import app
from science_buddy.services.research_routing import (
    ControlledRetrievalResult,
    ControlledRetrievalStep,
)


class EmptyResearchModel:
    name = "empty-research-model"

    async def generate_structured(
        self, _request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        raise AssertionError("No model call is needed when no candidates are available")

    async def stream_text(
        self, _system: str, _messages: Sequence[str]
    ) -> AsyncIterator[str]:
        yield ""


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_research_answer_persists_deterministic_audit_when_dynamic_is_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine, sessions = await make_database(tmp_path / "research-api.db")
    async with sessions() as session:
        project = Project(name="Research API project")
        session.add(project)
        await session.commit()
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    async def fake_resolve(*_args: object) -> Settings:
        return Settings(
            _env_file=None,
            LLM_PROVIDER="openai",
            LLM_MODEL="test-model",
            LLM_API_KEY="test-key",
            LLM_BASE_URL="https://example.test/v1",
        )

    def fake_build(*_args: object) -> EmptyResearchModel:
        return EmptyResearchModel()

    async def fake_execute(
        self: object, decision: object, *, limit: int, **_kwargs: object
    ) -> ControlledRetrievalResult:
        assert limit == 12
        typed_decision = decision  # keep the fake independent of implementation details
        return ControlledRetrievalResult(
            decision=typed_decision,  # type: ignore[arg-type]
            candidates=(),
            steps=(
                ControlledRetrievalStep(
                    subquery_id="q1",
                    focus="original",
                    query="BRAF evidence",
                    round=0,
                    candidates=(),
                    cache_level="miss",
                    report={"routes": []},
                ),
            ),
            followup_queries=(),
        )

    monkeypatch.setattr(research_api, "async_session_factory", sessions)
    monkeypatch.setattr(research_api, "resolve_model_settings", fake_resolve)
    monkeypatch.setattr(research_api, "build_model_provider", fake_build)
    monkeypatch.setattr(research_api.AdaptiveRetrievalService, "execute", fake_execute)
    app.dependency_overrides[research_api.get_session] = override_session
    app.state.cache = object()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/research/answer",
                json={
                    "project_id": str(project_id),
                    "question": "BRAF evidence",
                    "dynamic_planning": False,
                    "model_depth": "quick",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["research_plan"]["source"] == "deterministic"
    assert payload["research_plan"]["max_rounds"] == 0
    assert payload["research_actions"] == []
    assert len(payload["sufficiency"]) == 1
    assert [value["step_type"] for value in payload["progress"]] == ["route", "verify"]
    assert payload["result"]["claims"] == []

    async with sessions() as session:
        run = await session.scalar(select(ResearchRun))
        plans = list((await session.scalars(select(ResearchPlanSnapshot))).all())
        sufficiency = list((await session.scalars(select(SufficiencyAssessment))).all())
        steps = list(
            (
                await session.scalars(
                    select(ResearchStepMemory).order_by(ResearchStepMemory.step_number)
                )
            ).all()
        )

    assert run is not None and run.status == "insufficient_evidence"
    assert len(plans) == 1
    assert len(sufficiency) == 1
    assert [value.step_number for value in steps] == [1, 2]
    await engine.dispose()  # type: ignore[attr-defined]