import io
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import workbench as workbench_api
from science_buddy.config import Settings
from science_buddy.infrastructure.models import (
    Base,
    Project,
    WorkbenchAttachment,
    WorkbenchNote,
    WorkbenchTask,
)
from science_buddy.main import app


class FakeOcrService:
    async def extract_text(self, _image_path: Path) -> tuple[str, float]:
        return "BRAF V600E\n本地 OCR 识别文本", 0.98


def image_bytes() -> bytes:
    image = Image.new("RGB", (240, 100), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_empty_plot_intent_enables_automatic_model_planning() -> None:
    resolved = workbench_api._resolved_plot_intent("")

    assert "自动选择" in resolved
    assert "重复实验语义" in resolved
    assert workbench_api._resolved_plot_intent("  强调趋势  ") == "强调趋势"


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_workbench_note_task_calendar_and_local_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await make_database(tmp_path / "workbench.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")
    async with sessions() as session:
        project = Project(name="Workbench project")
        session.add(project)
        await session.commit()
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    monkeypatch.setattr(workbench_api, "get_ocr_service", lambda: FakeOcrService())
    app.dependency_overrides[workbench_api.get_session] = override_session
    app.dependency_overrides[workbench_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.get(
                "/api/v1/workbench/overview",
                params={"project_id": str(project_id), "month": "2026-08"},
            )
            note = await client.put(
                "/api/v1/workbench/days/2026-08-03",
                json={
                    "project_id": str(project_id),
                    "title": "噬菌体课题记录",
                    "content_markdown": (
                        "# 今日结论\n\n| 样本 | 结果 |\n"
                        "| --- | --- |\n| A | 阳性 |"
                    ),
                },
            )
            task = await client.post(
                "/api/v1/workbench/tasks",
                json={
                    "project_id": str(project_id),
                    "work_date": "2026-08-03",
                    "title": "复核实验结果",
                    "priority": "high",
                },
            )
            task_id = task.json()["id"]
            completed = await client.patch(
                f"/api/v1/workbench/tasks/{task_id}",
                params={"project_id": str(project_id)},
                json={"status": "done"},
            )
            attachment = await client.post(
                "/api/v1/workbench/days/2026-08-03/attachments",
                data={"project_id": str(project_id), "run_ocr": "true"},
                files={"file": ("figure.png", image_bytes(), "image/png")},
            )
            attachment_id = attachment.json()["id"]
            content = await client.get(
                f"/api/v1/workbench/attachments/{attachment_id}/content"
            )
            day = await client.get(
                "/api/v1/workbench/days/2026-08-03",
                params={"project_id": str(project_id)},
            )
            overview = await client.get(
                "/api/v1/workbench/overview",
                params={"project_id": str(project_id), "month": "2026-08"},
            )
            deleted_note = await client.delete(
                "/api/v1/workbench/days/2026-08-03",
                params={"project_id": str(project_id)},
            )
    finally:
        app.dependency_overrides.clear()

    assert empty.status_code == 200
    assert empty.json()["days"] == []
    assert note.status_code == 200
    assert note.json()["title"] == "噬菌体课题记录"
    assert "| 样本 |" in note.json()["content_markdown"]
    assert task.status_code == 201
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"
    assert completed.json()["completed_at"] is not None
    assert attachment.status_code == 201, attachment.text
    assert attachment.json()["ocr_status"] == "complete"
    assert attachment.json()["attachment_kind"] == "uploaded_image"
    assert "BRAF V600E" in attachment.json()["ocr_text"]
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/png")
    assert day.status_code == 200
    assert day.json()["note"]["id"] == note.json()["id"]
    assert len(day.json()["attachments"]) == 1
    assert overview.status_code == 200
    assert overview.json()["days"] == [
        {
            "entry_date": "2026-08-03",
            "has_note": True,
            "note_title": "噬菌体课题记录",
            "tasks_total": 1,
            "tasks_done": 1,
        }
    ]
    assert deleted_note.status_code == 204

    async with sessions() as session:
        assert len(list((await session.scalars(select(WorkbenchNote))).all())) == 1
        assert len(list((await session.scalars(select(WorkbenchTask))).all())) == 1
        stored_note = await session.scalar(select(WorkbenchNote))
        assert stored_note is not None and stored_note.deleted_at is not None
        stored_attachment = await session.get(WorkbenchAttachment, UUID(attachment_id))
        assert stored_attachment is not None
        assert stored_attachment.deleted_at is not None
        assert Path(stored_attachment.storage_path).is_file()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_workbench_plot_agent_renders_and_rerenders_locally(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "workbench-plot.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")
    async with sessions() as session:
        project = Project(name="Plot project")
        session.add(project)
        await session.commit()
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[workbench_api.get_session] = override_session
    app.dependency_overrides[workbench_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    table = (
        "| 组别 | 均值 | SD |\n"
        "| --- | --- | --- |\n"
        "| 对照 | 1.2 | 0.1 |\n"
        "| 处理 A | 2.1 | 0.2 |\n"
        "| 处理 B | 2.8 | 0.25 |"
    )
    updated_table = table.replace("处理 A", "处理 Alpha")
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            note = await client.put(
                "/api/v1/workbench/days/2026-08-06",
                json={
                    "project_id": str(project_id),
                    "title": "重复实验绘图",
                    "content_markdown": f"# 数据\n\n{table}",
                },
            )
            preview = await client.post(
                "/api/v1/workbench/plots/preview",
                json={
                    "project_id": str(project_id),
                    "markdown_table": table,
                    "chart_type": "bar",
                    "allow_model_planning": False,
                },
            )
            created = await client.post(
                "/api/v1/workbench/days/2026-08-06/plots",
                json={
                    "project_id": str(project_id),
                    "markdown_table": table,
                    "title": "处理组响应",
                    "caption": "均值 ± SD",
                    "chart_type": "auto",
                    "allow_model_planning": False,
                    "palette": "npg",
                    "font_size": 11,
                    "line_width": 2.2,
                },
            )
            attachment_id = created.json()["attachment"]["id"]
            content = await client.get(
                f"/api/v1/workbench/attachments/{attachment_id}/content"
            )
            removed_text_edit = await client.patch(
                f"/api/v1/workbench/attachments/{attachment_id}/plot/text",
                params={"project_id": str(project_id)},
                json={"text_element_id": "title", "value": "removed"},
            )
            rerendered = await client.patch(
                f"/api/v1/workbench/attachments/{attachment_id}/plot",
                params={"project_id": str(project_id)},
                json={
                    "markdown_table": updated_table,
                    "palette": "colorblind",
                    "font_size": 14,
                    "line_width": 3.0,
                    "show_grid": True,
                    "legend_position": "top",
                },
            )
            refreshed_day = await client.get(
                "/api/v1/workbench/days/2026-08-06",
                params={"project_id": str(project_id)},
            )
            deleted_plot = await client.delete(
                f"/api/v1/workbench/attachments/{attachment_id}",
                params={"project_id": str(project_id)},
            )
            restored_plot = await client.post(
                "/api/v1/workbench/days/2026-08-06/plots",
                json={
                    "project_id": str(project_id),
                    "markdown_table": updated_table,
                    "title": "处理组响应",
                    "caption": "均值 ± SD",
                    "chart_type": "bar",
                    "allow_model_planning": False,
                    "palette": "colorblind",
                    "font_size": 14,
                    "line_width": 3.0,
                    "show_grid": True,
                    "legend_position": "top",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert note.status_code == 200, note.text
    assert preview.status_code == 200, preview.text
    assert preview.content.startswith(b"\x89PNG")
    assert preview.headers["x-plot-chart-type"] == "bar"
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["chart_type"] == "bar"
    assert payload["planning_source"] == "deterministic"
    assert payload["attachment"]["attachment_kind"] == "generated_plot"
    assert payload["attachment"]["render_revision"] == 1
    assert payload["attachment"]["source_table_markdown"] == table
    assert payload["attachment"]["content_url"].endswith("?v=1")
    assert payload["attachment"]["render_spec"]["error_column"] == "SD"
    assert payload["attachment"]["render_spec"]["allow_model_planning"] is False
    assert content.status_code == 200
    assert content.content.startswith(b"\x89PNG")
    assert removed_text_edit.status_code == 404
    assert rerendered.status_code == 200, rerendered.text
    assert rerendered.json()["attachment"]["render_revision"] == 2
    assert rerendered.json()["attachment"]["content_url"].endswith("?v=2")
    assert rerendered.json()["attachment"]["render_spec"]["palette_name"] == (
        "colorblind"
    )
    assert rerendered.json()["attachment"]["render_spec"]["font_size"] == 14
    assert rerendered.json()["attachment"]["render_spec"]["allow_model_planning"] is False
    assert rerendered.json()["attachment"]["source_table_markdown"] == updated_table
    assert "处理 Alpha" in refreshed_day.json()["note"]["content_markdown"]
    assert deleted_plot.status_code == 204
    assert restored_plot.status_code == 201, restored_plot.text
    assert restored_plot.json()["attachment"]["id"] == attachment_id
    assert restored_plot.json()["attachment"]["render_revision"] == 3
    async with sessions() as session:
        all_attachments = list(
            (await session.scalars(select(WorkbenchAttachment))).all()
        )
        assert len(all_attachments) == 1
        attachment = await session.get(WorkbenchAttachment, UUID(attachment_id))
        assert attachment is not None
        assert "处理 Alpha" in attachment.source_table_markdown
        assert Path(attachment.storage_path).is_file()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_workbench_rejects_invalid_image(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "invalid-image.db")
    settings = Settings(_env_file=None, upload_directory=tmp_path / "uploads")
    async with sessions() as session:
        project = Project(name="Invalid image project")
        session.add(project)
        await session.commit()
        project_id = project.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    app.dependency_overrides[workbench_api.get_session] = override_session
    app.dependency_overrides[workbench_api.get_settings] = override_settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/workbench/days/2026-08-03/attachments",
                data={"project_id": str(project_id)},
                files={"file": ("fake.png", b"not-an-image", "image/png")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 415
    async with sessions() as session:
        assert await session.scalar(select(WorkbenchAttachment)) is None
    await engine.dispose()  # type: ignore[attr-defined]
