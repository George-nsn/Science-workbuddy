from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import rag as rag_api
from science_buddy.infrastructure.models import Base, Paper, Project, ProjectPaper
from science_buddy.main import app


class NoopCache:
    async def invalidate_namespace(self, _namespace: str) -> None:
        return None


@pytest.mark.asyncio
async def test_create_rag_collection_degrades_when_redis_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rag-api.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        project = Project(name="RAG API project")
        paper = Paper(title="Selected paper", authors=[], publication_types=[])
        session.add_all([project, paper])
        await session.flush()
        session.add(ProjectPaper(project_id=project.id, paper_id=paper.id))
        await session.commit()

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    async def no_redis(_collection, _settings):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(rag_api, "_enqueue_vector", no_redis)
    app.dependency_overrides[rag_api.get_session] = override_session
    app.state.cache = NoopCache()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/rag/collections",
                json={
                    "project_id": str(project.id),
                    "paper_ids": [str(paper.id)],
                    "name": None,
                    "build_vector": True,
                    "build_graph": True,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"].startswith("Knowledge Base ")
    assert payload["paper_count"] == 1
    assert payload["vector_status"] == "pending"
    assert payload["graph_status"] == "ready"
    assert "Redis is unavailable" in payload["warning"]
    await engine.dispose()
