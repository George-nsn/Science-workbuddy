from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import memory as memory_api
from science_buddy.infrastructure.models import (
    Base,
    CacheEntry,
    Project,
    WorkbenchNote,
    WorkbenchTask,
)
from science_buddy.main import app
from science_buddy.services.memory_lifecycle import (
    MemoryLifecycleService,
    cleanup_expired_cache_entries,
)


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_recycle_bin_restore_and_permanent_delete(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "memory-lifecycle.db")
    deleted_at = datetime.now(UTC)
    async with sessions() as session:
        project = Project(name="Lifecycle", trash_retention_days=14)
        session.add(project)
        await session.flush()
        note = WorkbenchNote(
            project_id=project.id,
            entry_date=deleted_at.date(),
            title="Deleted note",
            content_markdown="content",
            deleted_at=deleted_at,
        )
        task = WorkbenchTask(
            project_id=project.id,
            title="Deleted task",
            status="todo",
            priority="medium",
            deleted_at=deleted_at,
        )
        session.add_all([note, task])
        await session.commit()
        project_id = project.id
        note_id = note.id
        task_id = task.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    app.dependency_overrides[memory_api.get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            trash = await client.get(f"/api/v1/memory/projects/{project_id}/trash")
            restored = await client.post(
                f"/api/v1/memory/projects/{project_id}/trash/workbench_note/"
                f"{note_id}/restore"
            )
            purged = await client.delete(
                f"/api/v1/memory/projects/{project_id}/trash/workbench_task/{task_id}"
            )
    finally:
        app.dependency_overrides.clear()

    assert trash.status_code == 200
    assert trash.json()["retention_days"] == 14
    assert {item["entity_type"] for item in trash.json()["items"]} == {
        "workbench_note",
        "workbench_task",
    }
    assert restored.status_code == 204
    assert purged.status_code == 204
    async with sessions() as session:
        stored_note = await session.get(WorkbenchNote, note_id)
        assert stored_note is not None and stored_note.deleted_at is None
        assert await session.get(WorkbenchTask, task_id) is None
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cache_cleanup_and_retention_purge(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "memory-cleanup.db")
    now = datetime.now(UTC)
    async with sessions() as session:
        project = Project(name="Cleanup", trash_retention_days=7)
        session.add(project)
        await session.flush()
        session.add(
            WorkbenchTask(
                project_id=project.id,
                title="Expired trash",
                status="todo",
                priority="medium",
                deleted_at=now - timedelta(days=8),
            )
        )
        session.add_all(
            [
                CacheEntry(namespace="test", cache_key="old", value={}, expires_at=now),
                CacheEntry(
                    namespace="test",
                    cache_key="new",
                    value={},
                    expires_at=now + timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

    assert await cleanup_expired_cache_entries(sessions, now) == 1
    async with sessions() as session:
        assert await MemoryLifecycleService(session).purge_expired_trash(now) == 1
        cache_keys = set((await session.scalars(select(CacheEntry.cache_key))).all())
        tasks = list((await session.scalars(select(WorkbenchTask))).all())
    assert cache_keys == {"new"}
    assert tasks == []
    await engine.dispose()  # type: ignore[attr-defined]