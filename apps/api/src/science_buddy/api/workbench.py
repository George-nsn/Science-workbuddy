import asyncio
import hashlib
import io
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    WorkbenchAttachmentResponse,
    WorkbenchDayResponse,
    WorkbenchDaySummary,
    WorkbenchNoteResponse,
    WorkbenchNoteUpsertRequest,
    WorkbenchOverviewResponse,
    WorkbenchPlotRequest,
    WorkbenchPlotRerenderRequest,
    WorkbenchPlotResponse,
    WorkbenchTaskCreateRequest,
    WorkbenchTaskResponse,
    WorkbenchTaskUpdateRequest,
)
from science_buddy.config import Settings, get_settings
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import (
    Project,
    WorkbenchAttachment,
    WorkbenchNote,
    WorkbenchTask,
)
from science_buddy.services.models import (
    ModelConfigurationError,
    ModelResponseError,
    build_model_provider,
    drain_model_usage,
    resolve_model_settings,
)
from science_buddy.services.ocr import OcrUnavailableError, get_ocr_service
from science_buddy.services.plot_agent import (
    ParsedTable,
    PlotAgentError,
    PlotRequest,
    parse_markdown_table,
    render_plot,
)
from science_buddy.services.plot_planning import PlotPlanningService
from science_buddy.services.usage import UsageService

router = APIRouter(prefix="/workbench", tags=["workbench"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_IMAGE_PIXELS = 25_000_000


def _plot_request(
    payload: WorkbenchPlotRequest | WorkbenchPlotRerenderRequest,
    *,
    fallback: dict[str, object] | None = None,
) -> PlotRequest:
    values = fallback or {}

    def selected(name: str, value: object) -> object:
        return value if value is not None else values.get(name)

    def number(name: str, value: object, default: float) -> float:
        selected_value = selected(name, value)
        return (
            float(selected_value)
            if isinstance(selected_value, int | float | str)
            else default
        )

    return PlotRequest(
        chart_type=cast(
            Literal["auto", "bar", "line", "scatter", "distribution", "heatmap"],
            selected("chart_type", payload.chart_type) or "auto",
        ),
        title=cast(str | None, selected("title", payload.title)),
        caption=cast(str | None, selected("caption", payload.caption)),
        x_column=cast(str | None, selected("x_column", payload.x_column)),
        y_columns=tuple(
            cast(list[str], selected("y_columns", payload.y_columns) or [])
        ),
        group_column=cast(str | None, selected("group_column", payload.group_column)),
        error_column=cast(str | None, selected("error_column", payload.error_column)),
        font_size=int(number("font_size", payload.font_size, 10)),
        palette=str(
            payload.palette
            if payload.palette is not None
            else values.get("palette_name") or values.get("palette") or "journal"
        ),
        line_width=number("line_width", payload.line_width, 1.8),
        point_size=number("point_size", payload.point_size, 32),
        figure_width=number("figure_width", payload.figure_width, 7.2),
        figure_height=number("figure_height", payload.figure_height, 4.6),
        dpi=int(number("dpi", payload.dpi, 300)),
        show_grid=bool(selected("show_grid", payload.show_grid) or False),
        legend_position=str(
            selected("legend_position", payload.legend_position) or "best"
        ),
        data_layout=cast(
            Literal["auto", "long", "wide", "summary"],
            selected("data_layout", payload.data_layout) or "auto",
        ),
        condition_column=cast(
            str | None, selected("condition_column", payload.condition_column)
        ),
        value_column=cast(str | None, selected("value_column", payload.value_column)),
        replicate_column=cast(
            str | None, selected("replicate_column", payload.replicate_column)
        ),
        replicate_columns=tuple(
            cast(
                list[str],
                selected("replicate_columns", payload.replicate_columns) or [],
            )
        ),
        summary_stat=cast(
            Literal["mean_sd", "mean_sem", "mean_ci95"],
            selected("summary_stat", payload.summary_stat) or "mean_sd",
        ),
        show_all_points=bool(
            selected("show_all_points", payload.show_all_points)
            if selected("show_all_points", payload.show_all_points) is not None
            else True
        ),
        show_sample_size=bool(
            selected("show_sample_size", payload.show_sample_size)
            if selected("show_sample_size", payload.show_sample_size) is not None
            else True
        ),
        replicate_unit=cast(
            Literal["biological", "technical"],
            selected("replicate_unit", payload.replicate_unit) or "biological",
        ),
        pairing_mode=cast(
            Literal["independent", "paired"],
            selected("pairing_mode", payload.pairing_mode) or "independent",
        ),
    )


async def _apply_model_plot_plan(
    *,
    session: AsyncSession,
    settings: Settings,
    project_id: UUID,
    table: ParsedTable,
    request: PlotRequest,
    intent: str,
) -> tuple[PlotRequest, str | None]:
    return await _run_model_plot_plan(
        session=session,
        settings=settings,
        project_id=project_id,
        table=table,
        request=request,
        resolved_intent=_resolved_plot_intent(intent),
    )


def _resolved_plot_intent(intent: str) -> str:
    return intent.strip() or (
        "请根据表格结构、列类型和重复实验语义，自动选择最清晰的论文级图形、"
        "列映射与视觉样式；保留当前明确指定的重复类型、配对关系和统计方式。"
    )


async def _run_model_plot_plan(
    *,
    session: AsyncSession,
    settings: Settings,
    project_id: UUID,
    table: ParsedTable,
    request: PlotRequest,
    resolved_intent: str,
) -> tuple[PlotRequest, str | None]:
    model_settings = await resolve_model_settings(session, settings)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(180, connect=30),
        follow_redirects=True,
    ) as client:
        provider = build_model_provider(client, model_settings)
        plan = await PlotPlanningService(provider).plan(
            table=table,
            intent=resolved_intent,
            base_request=request,
        )
        await UsageService(session).record_model_events(
            project_id=project_id,
            events=drain_model_usage(provider),
        )
    return (
        PlotRequest(
            chart_type=plan.chart_type,
            title=plan.title or request.title,
            caption=plan.caption or request.caption,
            x_column=plan.x_column,
            y_columns=tuple(plan.y_columns),
            group_column=plan.group_column,
            error_column=plan.error_column,
            font_size=plan.font_size or request.font_size,
            palette=plan.palette,
            line_width=plan.line_width or request.line_width,
            point_size=plan.point_size or request.point_size,
            figure_width=plan.figure_width or request.figure_width,
            figure_height=plan.figure_height or request.figure_height,
            dpi=request.dpi,
            show_grid=(
                plan.show_grid if plan.show_grid is not None else request.show_grid
            ),
            legend_position=plan.legend_position or request.legend_position,
            data_layout=request.data_layout,
            condition_column=request.condition_column,
            value_column=request.value_column,
            replicate_column=request.replicate_column,
            replicate_columns=request.replicate_columns,
            summary_stat=request.summary_stat,
            show_all_points=request.show_all_points,
            show_sample_size=request.show_sample_size,
            replicate_unit=request.replicate_unit,
            pairing_mode=request.pairing_mode,
        ),
        plan.rationale,
    )


def _note_response(note: WorkbenchNote) -> WorkbenchNoteResponse:
    return WorkbenchNoteResponse(
        id=note.id,
        project_id=note.project_id,
        entry_date=note.entry_date,
        title=note.title,
        content_markdown=note.content_markdown,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _task_response(task: WorkbenchTask) -> WorkbenchTaskResponse:
    return WorkbenchTaskResponse(
        id=task.id,
        project_id=task.project_id,
        work_date=task.work_date,
        title=task.title,
        status=cast(Literal["todo", "in_progress", "done"], task.status),
        priority=cast(Literal["low", "medium", "high"], task.priority),
        completed_at=task.completed_at,
        source_kind=task.source_kind,
        source_id=task.source_id,
        source_section=task.source_section,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _attachment_response(attachment: WorkbenchAttachment) -> WorkbenchAttachmentResponse:
    revision = (
        f"?v={attachment.render_revision}"
        if attachment.attachment_kind == "generated_plot"
        else ""
    )
    return WorkbenchAttachmentResponse(
        id=attachment.id,
        project_id=attachment.project_id,
        note_id=attachment.note_id,
        filename=attachment.filename,
        media_type=attachment.media_type,
        byte_size=attachment.byte_size,
        width=attachment.width,
        height=attachment.height,
        ocr_status=cast(
            Literal["complete", "empty", "failed", "skipped"],
            attachment.ocr_status,
        ),
        ocr_text=attachment.ocr_text,
        ocr_error=attachment.ocr_error,
        attachment_kind=cast(
            Literal["uploaded_image", "generated_plot"], attachment.attachment_kind
        ),
        generator=attachment.generator,
        source_table_hash=attachment.source_table_hash,
        source_table_markdown=attachment.source_table_markdown,
        render_spec=attachment.render_spec,
        render_revision=attachment.render_revision,
        caption=attachment.caption,
        content_url=(
            f"/api/v1/workbench/attachments/{attachment.id}/content{revision}"
        ),
        created_at=attachment.created_at,
    )


async def _require_project(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _get_note(
    session: AsyncSession,
    *,
    project_id: UUID,
    entry_date: date,
    include_deleted: bool = False,
) -> WorkbenchNote | None:
    conditions = [
        WorkbenchNote.project_id == project_id,
        WorkbenchNote.entry_date == entry_date,
    ]
    if not include_deleted:
        conditions.append(WorkbenchNote.deleted_at.is_(None))
    return cast(
        WorkbenchNote | None,
        await session.scalar(
            select(WorkbenchNote).where(*conditions)
        )
    )


async def _ensure_note(
    session: AsyncSession,
    *,
    project_id: UUID,
    entry_date: date,
) -> WorkbenchNote:
    note = await _get_note(
        session,
        project_id=project_id,
        entry_date=entry_date,
        include_deleted=True,
    )
    if note is None:
        note = WorkbenchNote(
            project_id=project_id,
            entry_date=entry_date,
            title=f"{entry_date:%Y-%m-%d} 科研笔记",
            content_markdown="",
        )
        session.add(note)
        await session.flush()
    elif note.deleted_at is not None:
        note.deleted_at = None
        note.delete_reason = None
    return note


def _parse_month(value: str) -> tuple[date, date]:
    if not _MONTH_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="month must use YYYY-MM format")
    try:
        start = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="month is not valid") from exc
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def _replace_note_plot_url(
    content: str,
    attachment_id: UUID,
    revision: int,
) -> str:
    pattern = re.compile(
        rf"((?:https?://[^/\s)]+)?/api/v1/workbench/attachments/"
        rf"{re.escape(str(attachment_id))}/content)(?:\?v=\d+)?"
    )
    return pattern.sub(rf"\1?v={revision}", content)


@router.get("/overview", response_model=WorkbenchOverviewResponse)
async def get_workbench_overview(
    session: SessionDependency,
    project_id: Annotated[UUID, Query()],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> WorkbenchOverviewResponse:
    project = await _require_project(session, project_id)
    month_start, month_end = _parse_month(month)
    month_notes = list(
        (
            await session.scalars(
                select(WorkbenchNote)
                .where(
                    WorkbenchNote.project_id == project_id,
                    WorkbenchNote.entry_date >= month_start,
                    WorkbenchNote.entry_date < month_end,
                    WorkbenchNote.deleted_at.is_(None),
                )
                .order_by(WorkbenchNote.entry_date)
            )
        ).all()
    )
    all_tasks = list(
        (
            await session.scalars(
                select(WorkbenchTask)
                .where(
                    WorkbenchTask.project_id == project_id,
                    WorkbenchTask.deleted_at.is_(None),
                )
                .order_by(WorkbenchTask.created_at.desc())
                .limit(300)
            )
        ).all()
    )
    recent_notes = list(
        (
            await session.scalars(
                select(WorkbenchNote)
                .where(
                    WorkbenchNote.project_id == project_id,
                    WorkbenchNote.deleted_at.is_(None),
                )
                .order_by(WorkbenchNote.updated_at.desc())
                .limit(8)
            )
        ).all()
    )
    notes_by_date = {note.entry_date: note for note in month_notes}
    tasks_by_date: dict[date, list[WorkbenchTask]] = {}
    for task in all_tasks:
        if task.work_date is not None and month_start <= task.work_date < month_end:
            tasks_by_date.setdefault(task.work_date, []).append(task)
    marked_dates = sorted(set(notes_by_date) | set(tasks_by_date))
    days = [
        WorkbenchDaySummary(
            entry_date=entry_date,
            has_note=entry_date in notes_by_date,
            note_title=(notes_by_date[entry_date].title if entry_date in notes_by_date else None),
            tasks_total=len(tasks_by_date.get(entry_date, [])),
            tasks_done=sum(task.status == "done" for task in tasks_by_date.get(entry_date, [])),
        )
        for entry_date in marked_dates
    ]
    ordered_tasks = sorted(
        all_tasks,
        key=lambda task: (
            task.status == "done",
            task.work_date is None,
            task.work_date or date.max,
            -int(task.created_at.timestamp()),
        ),
    )
    return WorkbenchOverviewResponse(
        project_id=project.id,
        project_name=project.name,
        month=month,
        days=days,
        tasks=[_task_response(task) for task in ordered_tasks],
        recent_notes=[_note_response(note) for note in recent_notes],
    )


@router.get("/days/{entry_date}", response_model=WorkbenchDayResponse)
async def get_workbench_day(
    entry_date: date,
    session: SessionDependency,
    project_id: Annotated[UUID, Query()],
) -> WorkbenchDayResponse:
    await _require_project(session, project_id)
    note = await _get_note(session, project_id=project_id, entry_date=entry_date)
    tasks = list(
        (
            await session.scalars(
                select(WorkbenchTask)
                .where(
                    WorkbenchTask.project_id == project_id,
                    WorkbenchTask.work_date == entry_date,
                    WorkbenchTask.deleted_at.is_(None),
                )
                .order_by(WorkbenchTask.created_at)
            )
        ).all()
    )
    attachments: Sequence[WorkbenchAttachment] = ()
    if note is not None:
        attachments = (
            await session.scalars(
                select(WorkbenchAttachment)
                .where(
                    WorkbenchAttachment.note_id == note.id,
                    WorkbenchAttachment.deleted_at.is_(None),
                )
                .order_by(WorkbenchAttachment.created_at)
            )
        ).all()
    return WorkbenchDayResponse(
        project_id=project_id,
        entry_date=entry_date,
        note=_note_response(note) if note else None,
        tasks=[_task_response(task) for task in tasks],
        attachments=[_attachment_response(item) for item in attachments],
    )


@router.put("/days/{entry_date}", response_model=WorkbenchNoteResponse)
async def upsert_workbench_note(
    entry_date: date,
    request: WorkbenchNoteUpsertRequest,
    session: SessionDependency,
) -> WorkbenchNoteResponse:
    await _require_project(session, request.project_id)
    note = await _get_note(
        session,
        project_id=request.project_id,
        entry_date=entry_date,
        include_deleted=True,
    )
    if note is None:
        note = WorkbenchNote(
            project_id=request.project_id,
            entry_date=entry_date,
            title=request.title,
            content_markdown=request.content_markdown,
        )
        session.add(note)
    else:
        note.deleted_at = None
        note.delete_reason = None
        note.title = request.title
        note.content_markdown = request.content_markdown
    await session.commit()
    await session.refresh(note)
    return _note_response(note)


@router.delete("/days/{entry_date}", status_code=204)
async def delete_workbench_note(
    entry_date: date,
    session: SessionDependency,
    project_id: Annotated[UUID, Query()],
) -> Response:
    note = await _get_note(session, project_id=project_id, entry_date=entry_date)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    deleted_at = datetime.now(UTC)
    attachments = (
        await session.scalars(
            select(WorkbenchAttachment).where(
                WorkbenchAttachment.note_id == note.id,
                WorkbenchAttachment.deleted_at.is_(None),
            )
        )
    ).all()
    for attachment in attachments:
        attachment.deleted_at = deleted_at
        attachment.delete_reason = "parent_note_deleted"
    note.deleted_at = deleted_at
    note.delete_reason = "user_deleted"
    await session.commit()
    return Response(status_code=204)


@router.post("/tasks", response_model=WorkbenchTaskResponse, status_code=201)
async def create_workbench_task(
    request: WorkbenchTaskCreateRequest,
    session: SessionDependency,
) -> WorkbenchTaskResponse:
    await _require_project(session, request.project_id)
    task = WorkbenchTask(
        project_id=request.project_id,
        work_date=request.work_date,
        title=request.title,
        priority=request.priority,
        status="todo",
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return _task_response(task)


@router.patch("/tasks/{task_id}", response_model=WorkbenchTaskResponse)
async def update_workbench_task(
    task_id: UUID,
    request: WorkbenchTaskUpdateRequest,
    session: SessionDependency,
    project_id: Annotated[UUID, Query()],
) -> WorkbenchTaskResponse:
    task = await session.get(WorkbenchTask, task_id)
    if task is None or task.project_id != project_id or task.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Task not found")
    fields = request.model_fields_set
    if "title" in fields and request.title is not None:
        task.title = request.title
    if "work_date" in fields:
        task.work_date = request.work_date
    if "priority" in fields and request.priority is not None:
        task.priority = request.priority
    if "status" in fields and request.status is not None:
        task.status = request.status
        task.completed_at = (
            datetime.now(UTC) if request.status == "done" else None
        )
    await session.commit()
    await session.refresh(task)
    return _task_response(task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_workbench_task(
    task_id: UUID,
    session: SessionDependency,
    project_id: Annotated[UUID, Query()],
) -> Response:
    task = await session.get(WorkbenchTask, task_id)
    if task is None or task.project_id != project_id or task.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.deleted_at = datetime.now(UTC)
    task.delete_reason = "user_deleted"
    await session.commit()
    return Response(status_code=204)


async def _read_image(upload: UploadFile, max_bytes: int) -> bytes:
    payload = await upload.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HTTPException(status_code=413, detail="Image exceeds the configured size limit")
    if not payload:
        raise HTTPException(status_code=422, detail="Image is empty")
    return payload


def _normalize_image(payload: bytes, target: Path) -> tuple[int, int, int]:
    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions exceed the configured limit")
            if getattr(source, "is_animated", False):
                source.seek(0)
            normalized = source.convert("RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            normalized.save(target, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=415,
            detail="Only valid PNG, JPEG, or WebP images are supported",
        ) from exc
    return width, height, target.stat().st_size


@router.post(
    "/days/{entry_date}/attachments",
    response_model=WorkbenchAttachmentResponse,
    status_code=201,
)
async def upload_workbench_image(
    entry_date: date,
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    run_ocr: Annotated[bool, Form()] = True,
) -> WorkbenchAttachmentResponse:
    await _require_project(session, project_id)
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only PNG, JPEG, or WebP images are supported")
    payload = await _read_image(file, min(settings.max_upload_bytes, 10 * 1024 * 1024))
    content_hash = hashlib.sha256(payload).hexdigest()
    note = await _ensure_note(session, project_id=project_id, entry_date=entry_date)
    existing = await session.scalar(
        select(WorkbenchAttachment).where(
            WorkbenchAttachment.note_id == note.id,
            WorkbenchAttachment.content_hash == content_hash,
        )
    )
    if existing is not None:
        existing.deleted_at = None
        existing.delete_reason = None
        await session.commit()
        await session.refresh(existing)
        return _attachment_response(existing)

    attachment_id = uuid4()
    target = (
        settings.upload_directory
        / "workbench"
        / project_id.hex
        / note.id.hex
        / f"{attachment_id.hex}.png"
    )
    width, height, byte_size = await asyncio.to_thread(_normalize_image, payload, target)
    ocr_status = "skipped"
    ocr_text = ""
    ocr_error = None
    if run_ocr:
        try:
            ocr_text, _confidence = await get_ocr_service().extract_text(target)
            ocr_status = "complete" if ocr_text else "empty"
        except OcrUnavailableError as exc:
            ocr_status = "failed"
            ocr_error = str(exc)

    attachment = WorkbenchAttachment(
        id=attachment_id,
        project_id=project_id,
        note_id=note.id,
        filename=Path(file.filename or "research-image").name,
        media_type="image/png",
        storage_path=str(target),
        content_hash=content_hash,
        byte_size=byte_size,
        width=width,
        height=height,
        ocr_status=ocr_status,
        ocr_text=ocr_text,
        ocr_error=ocr_error,
        attachment_kind="uploaded_image",
    )
    session.add(attachment)
    try:
        await session.commit()
        await session.refresh(attachment)
    except Exception:
        await session.rollback()
        target.unlink(missing_ok=True)
        raise
    return _attachment_response(attachment)


@router.post(
    "/days/{entry_date}/plots",
    response_model=WorkbenchPlotResponse,
    status_code=201,
)
async def create_workbench_plot(
    entry_date: date,
    payload: WorkbenchPlotRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> WorkbenchPlotResponse:
    await _require_project(session, payload.project_id)
    try:
        table = parse_markdown_table(payload.markdown_table)
        request = _plot_request(payload)
        model_rationale: str | None = None
        planning_source: Literal["deterministic", "model"] = "deterministic"
        if payload.allow_model_planning:
            try:
                request, model_rationale = await _apply_model_plot_plan(
                    session=session,
                    settings=settings,
                    project_id=payload.project_id,
                    table=table,
                    request=request,
                    intent=payload.intent,
                )
                planning_source = "model"
            except (ModelConfigurationError, ModelResponseError, ValueError) as exc:
                model_rationale = (
                    f"LLM 规划未应用：{str(exc)[:400]}。已使用本地确定性图形识别。"
                )
        note = await _ensure_note(
            session,
            project_id=payload.project_id,
            entry_date=entry_date,
        )
        attachment_id = uuid4()
        target = (
            settings.upload_directory
            / "workbench"
            / payload.project_id.hex
            / note.id.hex
            / f"{attachment_id.hex}.png"
        )
        artifact = await asyncio.to_thread(render_plot, table, request, target)
    except PlotAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    attachment = await session.scalar(
        select(WorkbenchAttachment).where(
            WorkbenchAttachment.note_id == note.id,
            WorkbenchAttachment.content_hash == content_hash,
        )
    )
    created_new = attachment is None
    if attachment is None:
        attachment = WorkbenchAttachment(
            id=attachment_id,
            project_id=payload.project_id,
            note_id=note.id,
            filename=f"scientific-{artifact.chart_type}-{attachment_id.hex[:8]}.png",
            media_type="image/png",
            storage_path=str(target),
            content_hash=content_hash,
            byte_size=artifact.byte_size,
            width=artifact.width,
            height=artifact.height,
            ocr_status="skipped",
            ocr_text="",
            ocr_error=None,
            attachment_kind="generated_plot",
            generator="workbench-plot-agent-v2",
            source_table_hash=artifact.source_table_hash,
            source_table_markdown=payload.markdown_table,
            render_spec={
                **artifact.render_spec,
                "planning_source": planning_source,
                "model_rationale": model_rationale,
                "intent": payload.intent,
                "allow_model_planning": payload.allow_model_planning,
            },
            render_revision=1,
            caption=request.caption,
        )
        session.add(attachment)
    else:
        existing_target = Path(attachment.storage_path)
        existing_target.parent.mkdir(parents=True, exist_ok=True)
        if existing_target != target:
            target.replace(existing_target)
        attachment.deleted_at = None
        attachment.delete_reason = None
        attachment.filename = (
            f"scientific-{artifact.chart_type}-{attachment.id.hex[:8]}.png"
        )
        attachment.media_type = "image/png"
        attachment.byte_size = artifact.byte_size
        attachment.width = artifact.width
        attachment.height = artifact.height
        attachment.attachment_kind = "generated_plot"
        attachment.generator = "workbench-plot-agent-v2"
        attachment.source_table_hash = artifact.source_table_hash
        attachment.source_table_markdown = payload.markdown_table
        attachment.render_spec = {
            **artifact.render_spec,
            "planning_source": planning_source,
            "model_rationale": model_rationale,
            "intent": payload.intent,
            "allow_model_planning": payload.allow_model_planning,
        }
        attachment.render_revision += 1
        attachment.caption = request.caption
    try:
        await session.commit()
        await session.refresh(attachment)
    except Exception:
        await session.rollback()
        if created_new:
            target.unlink(missing_ok=True)
        raise
    response = _attachment_response(attachment)
    return WorkbenchPlotResponse(
        attachment=response,
        chart_type=artifact.chart_type,
        rationale=model_rationale or artifact.rationale,
        detected_columns=artifact.detected_columns,
        warnings=list(artifact.warnings),
        markdown_image=(
            f"\n![{attachment.caption or attachment.filename}]"
            f"({response.content_url})\n"
        ),
        planning_source=planning_source,
        model_rationale=model_rationale,
    )


@router.post("/plots/preview")
async def preview_workbench_plot(
    payload: WorkbenchPlotRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> Response:
    await _require_project(session, payload.project_id)
    target = settings.upload_directory / "workbench" / "preview" / f"{uuid4().hex}.png"
    try:
        table = parse_markdown_table(payload.markdown_table)
        request = _plot_request(payload)
        artifact = await asyncio.to_thread(render_plot, table, request, target)
        content = target.read_bytes()
    except PlotAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        target.unlink(missing_ok=True)
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Plot-Chart-Type": artifact.chart_type,
            "X-Plot-Planning-Source": "deterministic-preview",
            "X-Plot-Warnings": str(len(artifact.warnings)),
        },
    )


def _replace_note_table(
    content: str,
    previous_table: str,
    updated_table: str,
) -> str:
    candidates = (
        previous_table,
        previous_table.strip(),
        previous_table.replace("\r\n", "\n"),
    )
    for candidate in candidates:
        if candidate and candidate in content:
            return content.replace(candidate, updated_table, 1)
    try:
        expected_hash = parse_markdown_table(previous_table).source_hash
    except PlotAgentError:
        expected_hash = ""
    lines = content.splitlines()
    for index in range(len(lines) - 2):
        if not lines[index].strip().startswith("|") or "---" not in lines[index + 1]:
            continue
        cursor = index
        candidate_lines: list[str] = []
        while cursor < len(lines) and lines[cursor].strip().startswith("|"):
            candidate_lines.append(lines[cursor])
            cursor += 1
        candidate_table = "\n".join(candidate_lines)
        try:
            source_hash = parse_markdown_table(candidate_table).source_hash
        except PlotAgentError:
            continue
        if source_hash == expected_hash:
            return "\n".join([*lines[:index], updated_table, *lines[cursor:]])
    raise PlotAgentError(
        "The source table is no longer present in the note; open the plot editor to resync it"
    )


@router.patch(
    "/attachments/{attachment_id}/plot",
    response_model=WorkbenchPlotResponse,
)
async def rerender_workbench_plot(
    attachment_id: UUID,
    payload: WorkbenchPlotRerenderRequest,
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: Annotated[UUID, Query()],
) -> WorkbenchPlotResponse:
    attachment = await session.get(WorkbenchAttachment, attachment_id)
    if (
        attachment is None
        or attachment.project_id != project_id
        or attachment.deleted_at is not None
        or attachment.attachment_kind != "generated_plot"
        or not attachment.source_table_markdown
    ):
        raise HTTPException(status_code=404, detail="Generated plot not found")
    note = await session.get(WorkbenchNote, attachment.note_id)
    if note is None or note.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Workbench note not found")
    source_table_markdown = (
        payload.markdown_table
        if "markdown_table" in payload.model_fields_set and payload.markdown_table
        else attachment.source_table_markdown
    )
    if source_table_markdown != attachment.source_table_markdown:
        try:
            note.content_markdown = _replace_note_table(
                note.content_markdown,
                attachment.source_table_markdown,
                source_table_markdown,
            )
        except PlotAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        table = parse_markdown_table(source_table_markdown)
        request = _plot_request(payload, fallback=attachment.render_spec)
        model_rationale: str | None = None
        planning_source: Literal["deterministic", "model"] = "deterministic"
        if payload.allow_model_planning:
            try:
                request, model_rationale = await _apply_model_plot_plan(
                    session=session,
                    settings=settings,
                    project_id=project_id,
                    table=table,
                    request=request,
                    intent=payload.intent,
                )
                planning_source = "model"
            except (ModelConfigurationError, ModelResponseError, ValueError) as exc:
                model_rationale = (
                    f"LLM 样式建议未应用：{str(exc)[:400]}。已按手动设置重绘。"
                )
        target = Path(attachment.storage_path)
        temporary = target.with_suffix(".rerender.png")
        artifact = await asyncio.to_thread(render_plot, table, request, temporary)
        temporary.replace(target)
    except PlotAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    attachment.filename = f"scientific-{artifact.chart_type}-{attachment.id.hex[:8]}.png"
    attachment.content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    attachment.byte_size = target.stat().st_size
    attachment.width = artifact.width
    attachment.height = artifact.height
    attachment.source_table_hash = artifact.source_table_hash
    attachment.source_table_markdown = source_table_markdown
    attachment.render_spec = {
        **artifact.render_spec,
        "planning_source": planning_source,
        "model_rationale": model_rationale,
        "intent": payload.intent,
        "allow_model_planning": payload.allow_model_planning,
    }
    attachment.render_revision += 1
    attachment.caption = request.caption
    note.content_markdown = _replace_note_plot_url(
        note.content_markdown,
        attachment.id,
        attachment.render_revision,
    )
    await session.commit()
    await session.refresh(attachment)
    response = _attachment_response(attachment)
    return WorkbenchPlotResponse(
        attachment=response,
        chart_type=artifact.chart_type,
        rationale=model_rationale or artifact.rationale,
        detected_columns=artifact.detected_columns,
        warnings=list(artifact.warnings),
        markdown_image=(
            f"\n![{attachment.caption or attachment.filename}]"
            f"({response.content_url})\n"
        ),
        planning_source=planning_source,
        model_rationale=model_rationale,
    )


@router.get("/attachments/{attachment_id}/content")
async def get_workbench_attachment_content(
    attachment_id: UUID,
    session: SessionDependency,
    v: Annotated[int | None, Query(ge=0)] = None,
) -> FileResponse:
    _ = v
    attachment = await session.get(WorkbenchAttachment, attachment_id)
    if attachment is None or attachment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = Path(attachment.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing")
    return FileResponse(
        path,
        media_type=attachment.media_type,
        filename=attachment.filename,
        content_disposition_type="inline",
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_workbench_attachment(
    attachment_id: UUID,
    session: SessionDependency,
    project_id: Annotated[UUID, Query()],
) -> Response:
    attachment = await session.get(WorkbenchAttachment, attachment_id)
    if (
        attachment is None
        or attachment.project_id != project_id
        or attachment.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment.deleted_at = datetime.now(UTC)
    attachment.delete_reason = "user_deleted"
    await session.commit()
    return Response(status_code=204)
