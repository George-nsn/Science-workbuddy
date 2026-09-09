from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import brainstorm as brainstorm_api
from science_buddy.infrastructure.models import (
    Base,
    BrainstormMessage,
    Project,
    WorkbenchTask,
)
from science_buddy.main import app
from science_buddy.services.brainstorm_dispatch import extract_experimental_tasks
from science_buddy.services.brainstorm_sessions import BrainstormSessionService


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


def test_markdown_headings_become_hierarchical_experiment_tasks() -> None:
    message = BrainstormMessage(
        session_id=uuid4(),
        sequence_number=2,
        role="assistant",
        agent_name="coordinator",
        content=(
            "# 总方案\n\n"
            "## 样本准备\n准备病例与对照。\n\n"
            "### RNA 提取\n执行质控。\n\n"
            "## 统计分析\n进行预注册分析。"
        ),
        payload={},
        evidence_ids=[],
    )

    tasks = extract_experimental_tasks(message)

    assert [task.title for task in tasks] == ["样本准备", "RNA 提取", "统计分析"]
    assert [task.source_section for task in tasks] == [
        "样本准备",
        "样本准备 > RNA 提取",
        "统计分析",
    ]
    assert len({task.source_key for task in tasks}) == 3


def test_top_level_chapters_are_used_when_no_nested_sections_exist() -> None:
    message = BrainstormMessage(
        session_id=uuid4(),
        sequence_number=2,
        role="assistant",
        agent_name="coordinator",
        content="# 样本准备\n内容。\n\n# 检测与分析\n内容。",
        payload={},
        evidence_ids=[],
    )

    tasks = extract_experimental_tasks(message)

    assert [task.title for task in tasks] == ["样本准备", "检测与分析"]


@pytest.mark.asyncio
async def test_assistant_work_packages_dispatch_idempotently_to_workbench(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "brainstorm-dispatch.db")
    async with sessions() as session:
        project = Project(name="Dispatch project")
        session.add(project)
        await session.commit()
        service = BrainstormSessionService(session)
        brainstorm = await service.create(
            project_id=project.id,
            mode="exploration",
            title="Experiment plan",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )
        message = await service.append_message(
            session_id=brainstorm.id,
            role="assistant",
            agent_name="coordinator",
            content="## Fallback section\nThis is not used when work packages exist.",
            payload={
                "experiment_agent": {
                    "work_packages": [
                        {"title": "样本纳入与分组", "purpose": "Define groups"},
                        {"title": "盲法检测与质量控制", "purpose": "Measure endpoint"},
                    ]
                }
            },
        )
        await session.commit()
        session_id = brainstorm.id
        message_id = message.id
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    app.dependency_overrides[brainstorm_api.get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/messages/"
                f"{message_id}/dispatch-to-workbench",
                json={"work_date": "2026-08-05", "priority": "high"},
            )
            repeated = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/messages/"
                f"{message_id}/dispatch-to-workbench",
                json={"work_date": "2026-08-06", "priority": "low"},
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200, first.text
    assert first.json()["sections_detected"] == 2
    assert first.json()["created_count"] == 2
    assert first.json()["skipped_existing"] == 0
    assert [task["title"] for task in first.json()["tasks"]] == [
        "样本纳入与分组",
        "盲法检测与质量控制",
    ]
    assert {task["source_kind"] for task in first.json()["tasks"]} == {
        "brainstorm_message"
    }
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created_count"] == 0
    assert repeated.json()["skipped_existing"] == 2

    async with sessions() as session:
        tasks = list(
            (
                await session.scalars(
                    select(WorkbenchTask).where(WorkbenchTask.project_id == project_id)
                )
            ).all()
        )
    assert len(tasks) == 2
    assert {task.work_date.isoformat() if task.work_date else None for task in tasks} == {
        "2026-08-05"
    }
    assert {task.priority for task in tasks} == {"high"}
    assert {task.source_id for task in tasks} == {message_id}
    await engine.dispose()  # type: ignore[attr-defined]
