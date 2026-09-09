from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    SynthesisAgentRunResponse,
    SynthesisJobResponse,
    SynthesisReviewResponse,
    SynthesisSectionResponse,
    SynthesisSectionUpdateRequest,
    SynthesisSessionCreateRequest,
    SynthesisSessionDetailResponse,
    SynthesisSessionsResponse,
    SynthesisSessionSummaryResponse,
    SynthesisSourceResponse,
    SynthesisTextSourceRequest,
)
from science_buddy.config import Settings, get_settings
from science_buddy.domain.enums import JobStatus
from science_buddy.infrastructure.database import async_session_factory, get_session
from science_buddy.infrastructure.models import (
    Job,
    Project,
    SynthesisAgentRun,
    SynthesisReviewRound,
    SynthesisSection,
    SynthesisSession,
    SynthesisSource,
    WorkbenchAttachment,
)
from science_buddy.services.background_tasks import InProcessTaskManager
from science_buddy.services.exports import synthesis_docx, synthesis_markdown
from science_buddy.services.models import (
    ModelConfigurationError,
    ModelResponseError,
    build_model_provider,
    resolve_model_settings,
)
from science_buddy.services.synthesis import (
    SynthesisError,
    SynthesisOrchestrator,
    content_hash,
    extract_source_text,
    module_payload,
)
from science_buddy.services.synthesis_template import distilled_thesis_profile

router = APIRouter(prefix="/synthesis", tags=["synthesis"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
_ALLOWED_SOURCE_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown"}


async def _counts(session: AsyncSession, session_id: UUID) -> tuple[int, int]:
    section_count = int(
        await session.scalar(
            select(func.count()).select_from(SynthesisSection).where(
                SynthesisSection.session_id == session_id
            )
        )
        or 0
    )
    source_count = int(
        await session.scalar(
            select(func.count()).select_from(SynthesisSource).where(
                SynthesisSource.session_id == session_id
            )
        )
        or 0
    )
    return section_count, source_count


async def _summary(
    session: AsyncSession,
    value: SynthesisSession,
) -> SynthesisSessionSummaryResponse:
    section_count, source_count = await _counts(session, value.id)
    return SynthesisSessionSummaryResponse(
        id=value.id,
        project_id=value.project_id,
        title=value.title,
        topic=value.topic,
        status=cast(
            Literal["draft", "analyzing", "writing", "reviewing", "completed", "failed"],
            value.status,
        ),
        model_depth=cast(Literal["quick", "balanced", "deep", "max"], value.model_depth),
        max_context_tokens=value.max_context_tokens,
        max_review_rounds=value.max_review_rounds,
        include_workbench_notes=value.include_workbench_notes,
        allow_online_literature=value.allow_online_literature,
        current_round=value.current_round,
        section_count=section_count,
        source_count=source_count,
        error_message=value.error_message,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _source(value: SynthesisSource) -> SynthesisSourceResponse:
    return SynthesisSourceResponse(
        id=value.id,
        source_type=cast(
            Literal["workbench_note", "uploaded_file", "user_text"],
            value.source_type,
        ),
        source_ref_id=value.source_ref_id,
        filename=value.filename,
        media_type=value.media_type,
        content_hash=value.content_hash,
        module_count=len(value.module_index),
        summary=value.summary,
        relations=value.relations,
        status=value.status,
        error_message=value.error_message,
        created_at=value.created_at,
    )


def _section(value: SynthesisSection) -> SynthesisSectionResponse:
    return SynthesisSectionResponse(
        id=value.id,
        parent_id=value.parent_id,
        section_key=value.section_key,
        ordinal=value.ordinal,
        level=value.level,
        title=value.title,
        purpose=value.purpose,
        outline=value.outline,
        draft_markdown=value.draft_markdown,
        section_summary=value.section_summary,
        source_ids=value.source_ids,
        evidence_ids=value.evidence_ids,
        status=value.status,
        revision=value.revision,
        updated_at=value.updated_at,
    )


async def _detail(
    session: AsyncSession,
    value: SynthesisSession,
) -> SynthesisSessionDetailResponse:
    base = await _summary(session, value)
    sources = list(
        (
            await session.scalars(
                select(SynthesisSource)
                .where(SynthesisSource.session_id == value.id)
                .order_by(SynthesisSource.created_at)
            )
        ).all()
    )
    sections = list(
        (
            await session.scalars(
                select(SynthesisSection)
                .where(SynthesisSection.session_id == value.id)
                .order_by(SynthesisSection.ordinal)
            )
        ).all()
    )
    reviews = list(
        (
            await session.scalars(
                select(SynthesisReviewRound)
                .where(SynthesisReviewRound.session_id == value.id)
                .order_by(SynthesisReviewRound.round_number, SynthesisReviewRound.created_at)
            )
        ).all()
    )
    runs = list(
        (
            await session.scalars(
                select(SynthesisAgentRun)
                .where(SynthesisAgentRun.session_id == value.id)
                .order_by(SynthesisAgentRun.created_at)
            )
        ).all()
    )
    return SynthesisSessionDetailResponse(
        **base.model_dump(),
        template_profile=value.template_profile,
        document_map=value.document_map,
        global_outline=value.global_outline,
        global_summary=value.global_summary,
        glossary=value.glossary,
        citation_ledger=value.citation_ledger,
        figure_manifest=value.figure_manifest,
        manuscript_markdown=value.manuscript_markdown,
        sources=[_source(item) for item in sources],
        sections=[_section(item) for item in sections],
        reviews=[
            SynthesisReviewResponse(
                id=item.id,
                section_id=item.section_id,
                round_number=item.round_number,
                scope=item.scope,
                verdict=item.verdict,
                feedback=item.feedback,
                affected_section_keys=item.affected_section_keys,
                created_at=item.created_at,
            )
            for item in reviews
        ],
        agent_runs=[
            SynthesisAgentRunResponse(
                id=item.id,
                section_id=item.section_id,
                round_number=item.round_number,
                agent_name=item.agent_name,
                output=item.output,
                status=item.status,
                error=item.error,
                created_at=item.created_at,
            )
            for item in runs
        ],
    )


async def _require_synthesis(
    session: AsyncSession,
    session_id: UUID,
) -> SynthesisSession:
    value = await session.get(SynthesisSession, session_id)
    if value is None or value.deleted_at is not None:
        raise HTTPException(status_code=404, detail="综述写作会话不存在")
    return value


@router.get("/template-profile")
async def get_template_profile() -> dict[str, object]:
    return distilled_thesis_profile()


@router.post("/sessions", response_model=SynthesisSessionSummaryResponse, status_code=201)
async def create_synthesis_session(
    payload: SynthesisSessionCreateRequest,
    session: SessionDependency,
) -> SynthesisSessionSummaryResponse:
    project = await session.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    title = " ".join((payload.title or payload.topic).split())[:300]
    value = SynthesisSession(
        project_id=payload.project_id,
        title=title,
        topic=payload.topic.strip(),
        status="draft",
        model_depth=payload.model_depth,
        max_context_tokens=payload.max_context_tokens,
        max_review_rounds=payload.max_review_rounds,
        include_workbench_notes=payload.include_workbench_notes,
        allow_online_literature=payload.allow_online_literature,
        template_profile=distilled_thesis_profile(),
    )
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return await _summary(session, value)


@router.get("/sessions", response_model=SynthesisSessionsResponse)
async def list_synthesis_sessions(
    project_id: UUID,
    session: SessionDependency,
) -> SynthesisSessionsResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    values = list(
        (
            await session.scalars(
                select(SynthesisSession)
                .where(
                    SynthesisSession.project_id == project_id,
                    SynthesisSession.deleted_at.is_(None),
                )
                .order_by(SynthesisSession.updated_at.desc())
            )
        ).all()
    )
    return SynthesisSessionsResponse(
        project_id=project_id,
        sessions=[await _summary(session, value) for value in values],
    )


@router.get("/sessions/{session_id}", response_model=SynthesisSessionDetailResponse)
async def get_synthesis_session(
    session_id: UUID,
    session: SessionDependency,
) -> SynthesisSessionDetailResponse:
    return await _detail(session, await _require_synthesis(session, session_id))


@router.post("/sessions/{session_id}/sources/text", response_model=SynthesisSourceResponse)
async def add_synthesis_text_source(
    session_id: UUID,
    payload: SynthesisTextSourceRequest,
    session: SessionDependency,
) -> SynthesisSourceResponse:
    synthesis = await _require_synthesis(session, session_id)
    digest = content_hash(payload.content)
    existing = await session.scalar(
        select(SynthesisSource).where(
            SynthesisSource.session_id == synthesis.id,
            SynthesisSource.content_hash == digest,
        )
    )
    if existing is not None:
        return _source(existing)
    value = SynthesisSource(
        session_id=synthesis.id,
        source_type="user_text",
        filename=payload.title.strip(),
        media_type="text/markdown",
        content_hash=digest,
        extracted_text=payload.content.strip(),
        module_index=module_payload(payload.content),
        status="ready",
    )
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return _source(value)


@router.post("/sessions/{session_id}/sources/upload", response_model=SynthesisSourceResponse)
async def upload_synthesis_source(
    session_id: UUID,
    file: Annotated[UploadFile, File()],
    session: SessionDependency,
    settings: SettingsDependency,
) -> SynthesisSourceResponse:
    synthesis = await _require_synthesis(session, session_id)
    filename = Path(file.filename or "supplement.txt").name
    suffix = Path(filename).suffix.casefold()
    if suffix not in _ALLOWED_SOURCE_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持 PDF、DOCX、TXT 和 Markdown")
    payload = await file.read(settings.max_upload_bytes + 1)
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="补充材料超过文件大小限制")
    digest = hashlib.sha256(payload).hexdigest()
    existing = await session.scalar(
        select(SynthesisSource).where(
            SynthesisSource.session_id == synthesis.id,
            SynthesisSource.content_hash == digest,
        )
    )
    if existing is not None:
        return _source(existing)
    source_id = uuid4()
    target = (
        settings.upload_directory
        / "synthesis"
        / synthesis.project_id.hex
        / synthesis.id.hex
        / f"{source_id.hex}{suffix}"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    try:
        text = await asyncio.to_thread(
            extract_source_text,
            target,
            file.content_type or "application/octet-stream",
        )
    except (SynthesisError, UnicodeDecodeError) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    value = SynthesisSource(
        id=source_id,
        session_id=synthesis.id,
        source_type="uploaded_file",
        filename=filename,
        media_type=file.content_type or "application/octet-stream",
        storage_path=str(target),
        content_hash=digest,
        extracted_text=text,
        module_index=module_payload(text),
        status="ready",
    )
    session.add(value)
    await session.commit()
    await session.refresh(value)
    return _source(value)


def _job_response(value: Job) -> SynthesisJobResponse:
    return SynthesisJobResponse(
        id=value.id,
        session_id=UUID(str(value.payload["session_id"])),
        status=value.status.value,
        error_message=value.error_message,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


async def _run_synthesis_job(
    job_id: UUID,
    *,
    settings: Settings,
) -> None:
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED:
            return
        synthesis_id = UUID(str(job.payload["session_id"]))
        synthesis = await session.get(SynthesisSession, synthesis_id)
        if synthesis is None or synthesis.deleted_at is not None:
            job.status = JobStatus.CANCELLED
            job.error_message = "综述写作会话不存在或已删除"
            await session.commit()
            return
        job.status = JobStatus.RUNNING
        job.attempts += 1
        synthesis.status = "analyzing"
        synthesis.error_message = None
        await session.commit()
        try:
            model_settings = await resolve_model_settings(session, settings)
            timeout = httpx.Timeout(600, connect=30)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                model = build_model_provider(client, model_settings)
                await SynthesisOrchestrator(
                    session,
                    model=model,
                    settings=model_settings,
                    session_factory=async_session_factory,
                ).run(synthesis)
        except Exception as exc:
            await session.rollback()
            job = await session.get(Job, job_id)
            synthesis = await session.get(SynthesisSession, synthesis_id)
            message = (
                str(exc)[:2000]
                if isinstance(
                    exc,
                    SynthesisError
                    | ModelConfigurationError
                    | ModelResponseError
                    | ValueError,
                )
                else f"综述写作发生未预期错误：{type(exc).__name__}"
            )
            if job is not None:
                job.status = JobStatus.FAILED
                job.error_message = message
            if synthesis is not None:
                synthesis.status = "failed"
                synthesis.error_message = message
            await session.commit()
            return
        job = await session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.error_message = None
        await session.commit()


@router.post(
    "/sessions/{session_id}/run",
    response_model=SynthesisJobResponse,
    status_code=202,
)
async def run_synthesis_session(
    session_id: UUID,
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> SynthesisJobResponse:
    synthesis = await _require_synthesis(session, session_id)
    active = await session.scalar(
        select(Job)
        .where(
            Job.kind == "synthesis_manuscript",
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING]),
            Job.payload["session_id"].as_string() == str(session_id),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if active is not None:
        return _job_response(active)
    job = Job(
        kind="synthesis_manuscript",
        status=JobStatus.QUEUED,
        idempotency_key=f"synthesis:{session_id}:{uuid4().hex}",
        max_attempts=2,
        payload={"session_id": str(session_id)},
    )
    session.add(job)
    synthesis.status = "analyzing"
    synthesis.error_message = None
    await session.commit()
    manager = cast(InProcessTaskManager, request.app.state.background_tasks)
    manager.start(_run_synthesis_job(job.id, settings=settings))
    return _job_response(job)


@router.get(
    "/sessions/{session_id}/job",
    response_model=SynthesisJobResponse | None,
)
async def get_synthesis_job(
    session_id: UUID,
    session: SessionDependency,
) -> SynthesisJobResponse | None:
    await _require_synthesis(session, session_id)
    job = await session.scalar(
        select(Job)
        .where(
            Job.kind == "synthesis_manuscript",
            Job.payload["session_id"].as_string() == str(session_id),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return _job_response(job) if job else None


@router.patch(
    "/sessions/{session_id}/sections/{section_id}",
    response_model=SynthesisSectionResponse,
)
async def update_synthesis_section(
    session_id: UUID,
    section_id: UUID,
    payload: SynthesisSectionUpdateRequest,
    session: SessionDependency,
) -> SynthesisSectionResponse:
    synthesis = await _require_synthesis(session, session_id)
    section = await session.get(SynthesisSection, section_id)
    if section is None or section.session_id != synthesis.id:
        raise HTTPException(status_code=404, detail="章节不存在")
    section.draft_markdown = payload.draft_markdown.strip()
    section.content_hash = content_hash(section.draft_markdown)
    section.revision += 1
    section.status = "user_edited"
    sections = list(
        (
            await session.scalars(
                select(SynthesisSection)
                .where(SynthesisSection.session_id == synthesis.id)
                .order_by(SynthesisSection.ordinal)
            )
        ).all()
    )
    synthesis.manuscript_markdown = (
        f"# {synthesis.title}\n\n"
        + "\n\n".join(item.draft_markdown for item in sections if item.draft_markdown)
        + "\n"
    )
    await session.commit()
    await session.refresh(section)
    return _section(section)


@router.get("/sessions/{session_id}/figures/{figure_id}")
async def get_synthesis_figure(
    session_id: UUID,
    figure_id: str,
    session: SessionDependency,
    settings: SettingsDependency,
) -> FileResponse:
    synthesis = await _require_synthesis(session, session_id)
    figure = next(
        (
            item
            for item in synthesis.figure_manifest
            if str(item.get("figure_id") or "") == figure_id
        ),
        None,
    )
    if figure is None or not figure.get("storage_path"):
        raise HTTPException(status_code=404, detail="生成图表不存在")
    path = Path(str(figure["storage_path"])).resolve()
    allowed_root = (
        settings.upload_directory
        / "synthesis"
        / synthesis.project_id.hex
        / synthesis.id.hex
        / "figures"
    ).resolve()
    if not path.is_relative_to(allowed_root) or not path.is_file():
        raise HTTPException(status_code=404, detail="生成图表不存在")
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/sessions/{session_id}/export")
async def export_synthesis(
    session_id: UUID,
    session: SessionDependency,
    format: Annotated[Literal["markdown", "docx"], Query()] = "markdown",
) -> Response:
    synthesis = await _require_synthesis(session, session_id)
    if not synthesis.manuscript_markdown.strip():
        raise HTTPException(status_code=409, detail="当前还没有可导出的论文正文")
    if format == "markdown":
        content = synthesis_markdown(synthesis.title, synthesis.manuscript_markdown)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="synthesis-{session_id}.md"'},
        )
    figures: list[tuple[Path, str]] = []
    for item in synthesis.figure_manifest:
        storage_path = item.get("storage_path")
        if storage_path:
            figures.append(
                (
                    Path(str(storage_path)),
                    str(item.get("caption") or item.get("title") or "生成图表"),
                )
            )
            continue
        attachment_id = item.get("attachment_id")
        if not attachment_id:
            continue
        try:
            attachment = await session.get(WorkbenchAttachment, UUID(str(attachment_id)))
        except ValueError:
            continue
        if attachment is not None:
            caption = str(item.get("caption") or attachment.filename)
            figures.append((Path(attachment.storage_path), caption))
    content = synthesis_docx(
        synthesis.title,
        synthesis.manuscript_markdown,
        figures=figures,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="synthesis-{session_id}.docx"'},
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_synthesis_session(
    session_id: UUID,
    session: SessionDependency,
    settings: SettingsDependency,
) -> Response:
    value = await _require_synthesis(session, session_id)
    sources = list(
        (
            await session.scalars(
                select(SynthesisSource).where(SynthesisSource.session_id == value.id)
            )
        ).all()
    )
    session_root = (
        settings.upload_directory
        / "synthesis"
        / value.project_id.hex
        / value.id.hex
    ).resolve()
    paths = [
        Path(item.storage_path).resolve()
        for item in sources
        if item.storage_path
        and Path(item.storage_path).resolve().is_relative_to(session_root)
    ]
    generated_paths = [
        Path(str(item["storage_path"]))
        for item in value.figure_manifest
        if item.get("storage_path")
    ]
    paths.extend(
        path.resolve()
        for path in generated_paths
        if path.resolve().is_relative_to(session_root)
    )
    await session.delete(value)
    await session.commit()
    for path in paths:
        path.unlink(missing_ok=True)
    return Response(status_code=204)
