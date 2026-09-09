import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    BrainstormBackgroundJobResponse,
    BrainstormConfirmResponse,
    BrainstormMessageRequest,
    BrainstormMessageResponse,
    BrainstormPlanDiscoveryRequest,
    BrainstormPlanSnapshotResponse,
    BrainstormPreferenceRequest,
    BrainstormPrefetchRequest,
    BrainstormPrefetchResponse,
    BrainstormRegenerateRequest,
    BrainstormRestoreResponse,
    BrainstormSessionCreateRequest,
    BrainstormSessionDetail,
    BrainstormSessionMemoryResponse,
    BrainstormSessionRenameRequest,
    BrainstormSessionsResponse,
    BrainstormSessionSummary,
    BrainstormSessionUsageResponse,
    BrainstormSourceUploadResponse,
    BrainstormTaskDispatchRequest,
    BrainstormTaskDispatchResponse,
    BrainstormTurnResponse,
    BrainstormTurnUsageResponse,
    BrainstormVersionResponse,
    PlanDirectionResponse,
    PreferenceQuestionResponse,
    ProjectFactResponse,
    UsageSummaryResponse,
    WorkbenchTaskResponse,
)
from science_buddy.config import Settings, get_settings
from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.enums import JobStatus
from science_buddy.infrastructure.database import async_session_factory, get_session
from science_buddy.infrastructure.models import (
    BrainstormSession,
    Job,
    Project,
    UsageEvent,
    WorkbenchTask,
)
from science_buddy.services.adaptive_retrieval import AdaptiveRetrievalService
from science_buddy.services.background_tasks import InProcessTaskManager
from science_buddy.services.brainstorm import BrainstormOrchestrator
from science_buddy.services.brainstorm_dispatch import BrainstormTaskDispatchService
from science_buddy.services.brainstorm_plan import BrainstormPlanService
from science_buddy.services.brainstorm_sessions import BrainstormSessionService, SessionHistory
from science_buddy.services.brainstorm_types import PreferenceProfile
from science_buddy.services.cache import ThreeLevelCache
from science_buddy.services.documents import DocumentParseError, TextPdfParser
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.literature.common import LiteratureProviderError
from science_buddy.services.models import (
    ModelConfigurationError,
    ModelResponseError,
    build_model_provider,
    resolve_model_settings,
)
from science_buddy.services.project_memory import ProjectMemoryService
from science_buddy.services.query_planning import DeterministicQueryPlanner
from science_buddy.services.reranking import get_reranker_service
from science_buddy.services.research_literature import ResearchLiteratureSupplementer
from science_buddy.services.research_routing import (
    ControlledRetrievalStep,
    DeterministicResearchRouter,
)
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever
from science_buddy.services.retrieval_cache import retrieve_with_cache
from science_buddy.services.route_memory import RetrievalRouteMemoryService
from science_buddy.services.usage import UsageService, usage_summary
from science_buddy.services.web_search import (
    TavilySearchService,
    WebSearchError,
    resolve_web_search_settings,
)

router = APIRouter(prefix="/brainstorm", tags=["brainstorm"])
logger = logging.getLogger(__name__)
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def _session_summary(value: BrainstormSession) -> BrainstormSessionSummary:
    return BrainstormSessionSummary(
        id=value.id,
        project_id=value.project_id,
        collection_id=value.collection_id,
        title=value.title,
        mode=value.mode,
        status=value.status,
        session_number=value.session_number,
        confirmation_round=value.confirmation_round,
        allow_pubmed_search=value.allow_pubmed_search,
        model_processing_allowed=value.model_processing_allowed,
        workflow=cast(Literal["classic", "plan"], value.workflow),
        phase=value.phase,
        allow_web_search=value.allow_web_search,
        plan_snapshot=value.plan_snapshot,
        model_depth=cast(
            Literal["quick", "balanced", "deep", "max"], value.model_depth
        ),
        max_context_tokens=value.max_context_tokens,
        agent_background=value.agent_background,
        created_at=value.created_at.isoformat(),
    )


def _usage_response(values: dict[str, object]) -> UsageSummaryResponse:
    return UsageSummaryResponse.model_validate(values)


async def _session_usage(
    session_id: UUID,
    usage_service: UsageService,
) -> BrainstormSessionUsageResponse:
    events = await usage_service.session_events(session_id)
    by_turn: dict[int, list[UsageEvent]] = {}
    for event in events:
        if event.turn_number is not None:
            by_turn.setdefault(event.turn_number, []).append(event)
    return BrainstormSessionUsageResponse(
        session_id=session_id,
        summary=_usage_response(usage_summary(events)),
        turns=[
            BrainstormTurnUsageResponse(
                turn_number=turn,
                message_id=next(
                    (event.message_id for event in values if event.message_id is not None),
                    None,
                ),
                **usage_summary(values),
            )
            for turn, values in sorted(by_turn.items())
        ],
    )


async def _session_detail(
    history: SessionHistory,
    memory_service: ProjectMemoryService,
    usage_service: UsageService,
) -> BrainstormSessionDetail:
    memory = await memory_service.latest_session_memory(history.session.id)
    facts = await memory_service.list_facts(
        history.session.project_id,
        source_session_id=history.session.id,
        limit=100,
    )
    session_usage = await _session_usage(
        history.session.id,
        usage_service,
    )
    return BrainstormSessionDetail(
        session=_session_summary(history.session),
        messages=[
            BrainstormMessageResponse(
                id=value.id,
                sequence_number=value.sequence_number,
                role=value.role,
                agent_name=value.agent_name,
                content=value.content,
                payload=value.payload,
                evidence_ids=value.evidence_ids,
                created_at=value.created_at.isoformat(),
            )
            for value in history.messages
        ],
        versions=[
            BrainstormVersionResponse(
                id=value.id,
                version_number=value.version_number,
                kind=value.kind,
                parent_version_id=value.parent_version_id,
                content=value.content,
                source_filename=value.source_filename,
                change_summary=value.change_summary,
                created_at=value.created_at.isoformat(),
            )
            for value in history.versions
        ],
        memory=(
            BrainstormSessionMemoryResponse(
                session_id=memory.session_id,
                source_message_id=memory.source_message_id,
                turn_number=memory.turn_number,
                summary_markdown=memory.summary_markdown,
                summary_data=memory.summary_data,
                updated_at=memory.updated_at,
            )
            if memory
            else None
        ),
        facts=[
            ProjectFactResponse(
                id=value.id,
                project_id=value.project_id,
                category=value.category,
                statement=value.statement,
                source_type=value.source_type,
                source_id=value.source_id,
                source_session_id=value.source_session_id,
                source_locator=value.source_locator,
                confidence=value.confidence,
                importance=value.importance,
                status=cast(Literal["active", "superseded", "retracted"], value.status),
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
            for value in facts
        ],
        usage=session_usage,
    )


def _workbench_task_response(value: WorkbenchTask) -> WorkbenchTaskResponse:
    return WorkbenchTaskResponse(
        id=value.id,
        project_id=value.project_id,
        work_date=value.work_date,
        title=value.title,
        status=cast(Literal["todo", "in_progress", "done"], value.status),
        priority=cast(Literal["low", "medium", "high"], value.priority),
        completed_at=value.completed_at,
        source_kind=value.source_kind,
        source_id=value.source_id,
        source_section=value.source_section,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _plan_snapshot_response(value: BrainstormSession) -> BrainstormPlanSnapshotResponse:
    snapshot = value.plan_snapshot
    directions = snapshot.get("directions", [])
    questions = snapshot.get("preference_questions", [])
    return BrainstormPlanSnapshotResponse(
        session_id=value.id,
        phase=value.phase,
        directions=[
            PlanDirectionResponse.model_validate(item)
            for item in directions
            if isinstance(item, dict)
        ],
        preference_questions=[
            PreferenceQuestionResponse.model_validate(item)
            for item in questions
            if isinstance(item, dict)
        ],
        selected_direction_id=(
            str(snapshot["selected_direction_id"])
            if snapshot.get("selected_direction_id")
            else None
        ),
        preference_profile=cast(
            dict[str, object], snapshot.get("preference_profile", {})
        ),
        preference_answers=cast(
            dict[str, object], snapshot.get("preference_answers", {})
        ),
        readiness_score=float(snapshot.get("readiness_score", 0.0)),
        missing_fields=[str(item) for item in snapshot.get("missing_fields", [])],
        web_search_used=bool(snapshot.get("web_search_used", False)),
        prefetch_job=cast(
            dict[str, object] | None, snapshot.get("prefetch_job")
        ),
    )


def _background_job_response(value: Job) -> BrainstormBackgroundJobResponse:
    payload = value.payload
    return BrainstormBackgroundJobResponse(
        id=value.id,
        session_id=UUID(str(payload["session_id"])),
        kind=cast(
            Literal["plan_discovery", "plan_generation"],
            value.kind.removeprefix("brainstorm_"),
        ),
        status=value.status.value,
        error_message=value.error_message,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _set_plan_background_state(
    brainstorm: BrainstormSession,
    job: Job,
    *,
    error_message: str | None = None,
) -> None:
    snapshot = dict(brainstorm.plan_snapshot)
    snapshot["background_job"] = {
        "id": str(job.id),
        "kind": job.kind.removeprefix("brainstorm_"),
        "status": job.status.value,
        "error_message": error_message,
    }
    brainstorm.plan_snapshot = snapshot


def _safe_background_error(error: Exception) -> str:
    if isinstance(error, HTTPException):
        detail = str(error.detail)
        if "evidence outside the current candidate set" in detail.casefold():
            return (
                "Agent 引用了不属于本轮检索候选集的临时 Evidence ID。"
                "系统已升级为自动移除并标记证据不足，请重新生成。"
            )
        return detail[:2000]
    if isinstance(
        error,
        ModelConfigurationError | ModelResponseError | WebSearchError | ValueError,
    ):
        detail = str(error)
        if "evidence outside the current candidate set" in detail.casefold():
            return (
                "Agent 引用了不属于本轮检索候选集的临时 Evidence ID。"
                "系统已升级为自动移除并标记证据不足，请重新生成。"
            )
        return detail[:2000]
    raw = str(error).strip()
    if raw:
        return f"后台生成过程遇到异常：{raw[:500]}，请重试。"
    return "后台研究方向生成发生未预期错误，请查看 API 日志后重试。"


async def _retrieve_plan_evidence(
    *,
    query: str,
    history: SessionHistory,
    cache: ThreeLevelCache,
    session: AsyncSession,
    settings: Settings,
) -> list[RetrievalCandidate]:
    project = await session.get(Project, history.session.project_id)
    if project is None:
        raise ValueError("Project does not exist")
    workflow_id = uuid4()
    token_service = EvidenceTokenService(settings.evidence_signing_key.get_secret_value())
    retrieval_config = RetrievalConfig.from_settings(settings)
    query_plan = DeterministicQueryPlanner().plan(query)
    routing = DeterministicResearchRouter().route(
        query,
        max_subqueries=4,
        max_followup_rounds=0,
        identifier=query_plan.is_identifier_lookup,
    )
    try:
        execution = await AdaptiveRetrievalService(
            cache=cache,
            token_service=token_service,
            workflow_id=workflow_id,
            project_id=history.session.project_id,
            collection_id=history.session.collection_id,
            project_revision=project.retrieval_revision,
            config=retrieval_config,
            retriever_factory=lambda: SQLiteHybridRetriever(
                session,
                token_service,
                workflow_id=workflow_id,
                config=retrieval_config,
                embedding_service=get_embedding_service(),
                session_factory=async_session_factory,
                collection_id=history.session.collection_id,
                reranker=(
                    get_reranker_service(retrieval_config.reranker_model)
                    if retrieval_config.reranker_enabled
                    else None
                ),
            ),
            retrieve_cached=retrieve_with_cache,
        ).execute(routing, limit=12)
    except RuntimeError as exc:
        if str(exc) != "All retrieval routes failed":
            raise
        logger.warning(
            "brainstorm_plan_retrieval_degraded session_id=%s collection_id=%s reason=%s",
            history.session.id,
            history.session.collection_id,
            exc,
        )
        return []
    return list(execution.candidates)


async def _execute_plan_discovery(
    *,
    session_id: UUID,
    payload: BrainstormPlanDiscoveryRequest,
    cache: ThreeLevelCache,
    session: AsyncSession,
    settings: Settings,
) -> None:
    service = BrainstormSessionService(session)
    history = await service.history(session_id)
    evidence = await _retrieve_plan_evidence(
        query=payload.seed_interest,
        history=history,
        cache=cache,
        session=session,
        settings=settings,
    )
    if history.session.allow_pubmed_search:
        async with httpx.AsyncClient(
            timeout=settings.literature_request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            await ResearchLiteratureSupplementer(
                session,
                client,
                settings,
            ).supplement_brainstorm(
                project_id=history.session.project_id,
                session_id=session_id,
                session_number=history.session.session_number,
                queries=[payload.seed_interest],
                turn_number=0,
            )
        evidence = await _retrieve_plan_evidence(
            query=payload.seed_interest,
            history=history,
            cache=cache,
            session=session,
            settings=settings,
        )
    web_results = []
    if history.session.allow_web_search:
        resolved_search = await resolve_web_search_settings(session, settings)
        if resolved_search is None:
            raise ValueError("This session enabled web search, but Tavily is not configured")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            web_results = await TavilySearchService(client, resolved_search).search(
                payload.seed_interest,
                max_results=min(payload.max_directions + 2, 8),
            )
    model_settings = await resolve_model_settings(session, settings)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        model = build_model_provider(client, model_settings)
        await BrainstormPlanService(session).discover(
            brainstorm=history.session,
            seed_interest=payload.seed_interest,
            model=model,
            evidence=evidence,
            web_results=web_results,
        )


async def _run_plan_discovery_job(
    job_id: UUID,
    *,
    cache: ThreeLevelCache,
    settings: Settings,
) -> None:
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED:
            return
        session_id = UUID(str(job.payload["session_id"]))
        job.status = JobStatus.RUNNING
        job.attempts += 1
        brainstorm = await session.get(BrainstormSession, session_id)
        if brainstorm is None or brainstorm.deleted_at is not None:
            job.status = JobStatus.CANCELLED
            job.error_message = "头脑风暴会话不存在或已删除。"
            await session.commit()
            return
        _set_plan_background_state(brainstorm, job)
        await session.commit()
        try:
            await _execute_plan_discovery(
                session_id=session_id,
                payload=BrainstormPlanDiscoveryRequest.model_validate(job.payload["request"]),
                cache=cache,
                session=session,
                settings=settings,
            )
        except Exception as exc:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.exception(
                "brainstorm_plan_discovery_job_failed job_id=%s session_id=%s",
                job_id,
                session_id,
            )
            async with async_session_factory() as error_session:
                job_record = await error_session.get(Job, job_id)
                failed_session = await error_session.get(BrainstormSession, session_id)
                safe_error = _safe_background_error(exc)
                if job_record is not None:
                    job_record.status = JobStatus.FAILED
                    job_record.error_message = safe_error
                if failed_session is not None:
                    failed_session.status = "active"
                    failed_session.phase = "plan_directions"
                    if job_record is not None:
                        _set_plan_background_state(
                            failed_session,
                            job_record,
                            error_message=safe_error,
                        )
                await error_session.commit()
            return
        job = await session.get(Job, job_id)
        completed_session = await session.get(BrainstormSession, session_id)
        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.error_message = None
        if completed_session is not None:
            completed_session.status = "active"
            if job is not None:
                _set_plan_background_state(completed_session, job)
        await session.commit()


async def _run_plan_generation_job(
    job_id: UUID,
    *,
    cache: ThreeLevelCache,
    settings: Settings,
) -> None:
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED:
            return
        session_id = UUID(str(job.payload["session_id"]))
        job.status = JobStatus.RUNNING
        job.attempts += 1
        brainstorm = await session.get(BrainstormSession, session_id)
        if brainstorm is None or brainstorm.deleted_at is not None:
            job.status = JobStatus.CANCELLED
            job.error_message = "头脑风暴会话不存在或已删除。"
            await session.commit()
            return
        _set_plan_background_state(brainstorm, job)
        await session.commit()
        try:
            prompt = BrainstormPlanService.build_generation_request(brainstorm)
            result = await _execute_brainstorm_turn(
                session_id=session_id,
                payload=BrainstormMessageRequest(content=prompt),
                cache=cache,
                session=session,
                settings=settings,
                processing_already_claimed=True,
            )
            refreshed = await session.get(BrainstormSession, session_id)
            if refreshed is not None:
                BrainstormPlanService.mark_proposal_ready(
                    refreshed,
                    result.technical_route_mermaid,
                )
                await session.commit()
        except Exception as exc:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.exception(
                "brainstorm_plan_generation_job_failed job_id=%s session_id=%s",
                job_id,
                session_id,
            )
            async with async_session_factory() as error_session:
                job_record = await error_session.get(Job, job_id)
                failed_session = await error_session.get(BrainstormSession, session_id)
                safe_error = _safe_background_error(exc)
                if job_record is not None:
                    job_record.status = JobStatus.FAILED
                    job_record.error_message = safe_error
                if failed_session is not None:
                    failed_session.status = "active"
                    failed_session.phase = "ready_to_generate"
                    if job_record is not None:
                        _set_plan_background_state(
                            failed_session,
                            job_record,
                            error_message=safe_error,
                        )
                await error_session.commit()
            return
        job = await session.get(Job, job_id)
        completed_session = await session.get(BrainstormSession, session_id)
        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.error_message = None
        if completed_session is not None and job is not None:
            _set_plan_background_state(completed_session, job)
        await session.commit()


@router.post("/sessions", response_model=BrainstormSessionSummary)
async def create_brainstorm_session(
    payload: BrainstormSessionCreateRequest,
    session: SessionDependency,
) -> BrainstormSessionSummary:
    try:
        value = await BrainstormSessionService(session).create(
            project_id=payload.project_id,
            mode=payload.mode,
            title=payload.title,
            collection_id=payload.collection_id,
            allow_pubmed_search=payload.allow_pubmed_search,
            model_processing_allowed=payload.model_processing_allowed,
            workflow=payload.workflow,
            allow_web_search=payload.allow_web_search,
            model_depth=payload.model_depth,
            max_context_tokens=payload.max_context_tokens,
            agent_background=payload.agent_background,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_summary(value)


@router.get("/sessions", response_model=BrainstormSessionsResponse)
async def list_brainstorm_sessions(
    project_id: UUID,
    session: SessionDependency,
) -> BrainstormSessionsResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    values = await BrainstormSessionService(session).list_for_project(project_id)
    return BrainstormSessionsResponse(
        project_id=project_id,
        sessions=[_session_summary(value) for value in values],
    )


@router.get("/sessions/{session_id}", response_model=BrainstormSessionDetail)
async def get_brainstorm_session(
    session_id: UUID,
    session: SessionDependency,
) -> BrainstormSessionDetail:
    try:
        history = await BrainstormSessionService(session).history(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _session_detail(
        history,
        ProjectMemoryService(session),
        UsageService(session),
    )


@router.patch("/sessions/{session_id}", response_model=BrainstormSessionSummary)
async def rename_brainstorm_session(
    session_id: UUID,
    payload: BrainstormSessionRenameRequest,
    session: SessionDependency,
) -> BrainstormSessionSummary:
    try:
        value = await BrainstormSessionService(session).rename(session_id, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _session_summary(value)


@router.post(
    "/sessions/{session_id}/source",
    response_model=BrainstormSourceUploadResponse,
)
async def upload_brainstorm_source(
    session_id: UUID,
    file: Annotated[UploadFile, File()],
    session: SessionDependency,
    settings: SettingsDependency,
) -> BrainstormSourceUploadResponse:
    service = BrainstormSessionService(session)
    try:
        history = await service.history(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if history.session.mode != "refinement":
        raise HTTPException(status_code=409, detail="Source upload is only for refinement mode")
    if any(version.kind == "original" for version in history.versions):
        raise HTTPException(status_code=409, detail="The original proposal is immutable and exists")
    payload = await file.read(settings.max_upload_bytes + 1)
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Proposal file exceeds the size limit")
    filename = Path(file.filename or "proposal.txt").name
    media_type = file.content_type or "text/plain"
    try:
        if payload.startswith(b"%PDF-"):
            settings.upload_directory.mkdir(parents=True, exist_ok=True)
            temp_path = settings.upload_directory / f"brainstorm-{uuid4().hex}.pdf"
            temp_path.write_bytes(payload)
            try:
                parsed = await asyncio.to_thread(TextPdfParser().parse, temp_path)
                content = "\n\n".join(
                    segment.text
                    for section in parsed
                    for segment in section.segments
                )
            finally:
                temp_path.unlink(missing_ok=True)
        else:
            content = payload.decode("utf-8-sig")
    except (UnicodeDecodeError, DocumentParseError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Upload a UTF-8 text/Markdown file or a text-based PDF",
        ) from exc
    content = content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded proposal contains no text")
    if len(content) > 120000:
        raise HTTPException(status_code=413, detail="Extracted proposal text exceeds 120000 chars")
    version = await service.add_version(
        session_id=session_id,
        kind="original",
        content=content,
        parent_version_id=None,
        source_filename=filename,
        source_media_type=media_type,
        source_hash=hashlib.sha256(payload).hexdigest(),
    )
    await service.append_message(
        session_id=session_id,
        role="system",
        agent_name="version_control",
        content=f"已保存只读原始课题方案：{filename}",
        payload={"version_id": str(version.id), "version_number": version.version_number},
    )
    await session.commit()
    return BrainstormSourceUploadResponse(
        session_id=session_id,
        version_id=version.id,
        version_number=version.version_number,
        filename=filename,
        characters=len(content),
    )


@router.post("/sessions/{session_id}/messages", response_model=BrainstormTurnResponse)
async def send_brainstorm_message(
    session_id: UUID,
    payload: BrainstormMessageRequest,
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
    regenerated_from_message_id: UUID | None = None,
    regenerate_base_version_id: UUID | None = None,
) -> BrainstormTurnResponse:
    return await _execute_brainstorm_turn(
        session_id=session_id,
        payload=payload,
        cache=cast(ThreeLevelCache | None, getattr(request.app.state, "cache", None)),
        session=session,
        settings=settings,
        regenerated_from_message_id=regenerated_from_message_id,
        regenerate_base_version_id=regenerate_base_version_id,
    )


async def _execute_brainstorm_turn(
    *,
    session_id: UUID,
    payload: BrainstormMessageRequest,
    cache: ThreeLevelCache | None,
    session: AsyncSession,
    settings: Settings,
    regenerated_from_message_id: UUID | None = None,
    regenerate_base_version_id: UUID | None = None,
    processing_already_claimed: bool = False,
) -> BrainstormTurnResponse:
    service = BrainstormSessionService(session)
    try:
        history = await service.history(session_id)
        if not history.session.model_processing_allowed:
            raise ValueError(
                "This session has not authorized sending user/proposal context "
                "to the configured model"
            )
        if processing_already_claimed:
            if history.session.status != "processing":
                raise ValueError("The background brainstorm task lost its processing claim")
        else:
            await service.set_processing(history.session)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if cache is None:
        raise RuntimeError("Application cache is not initialized")

    workflow_id = uuid4()
    token_service = EvidenceTokenService(settings.evidence_signing_key.get_secret_value())
    retrieval_config = RetrievalConfig.from_settings(settings)
    retrieval_cache_levels: list[str] = []
    retrieval_step_audits: list[ControlledRetrievalStep] = []
    retrieval_failures: list[tuple[str, UUID | None, str]] = []

    async def retrieve_evidence(
        query: str, collection_id: UUID | None
    ) -> list[RetrievalCandidate]:
        project = await session.get(Project, history.session.project_id)
        if project is None:
            raise ValueError("Project does not exist")
        routing = DeterministicResearchRouter().route(
            query,
            max_subqueries=3,
            max_followup_rounds=0,
        )
        try:
            execution = await AdaptiveRetrievalService(
                cache=cache,
                token_service=token_service,
                workflow_id=workflow_id,
                project_id=history.session.project_id,
                collection_id=collection_id,
                project_revision=project.retrieval_revision,
                config=retrieval_config,
                retriever_factory=lambda: SQLiteHybridRetriever(
                    session,
                    token_service,
                    workflow_id=workflow_id,
                    config=retrieval_config,
                    embedding_service=get_embedding_service(),
                    session_factory=async_session_factory,
                    collection_id=collection_id,
                    reranker=(
                        get_reranker_service(retrieval_config.reranker_model)
                        if retrieval_config.reranker_enabled
                        else None
                    ),
                ),
                retrieve_cached=retrieve_with_cache,
            ).execute(routing, limit=12)
        except RuntimeError as exc:
            if str(exc) != "All retrieval routes failed":
                raise
            retrieval_cache_levels.append("miss")
            logger.warning(
                "brainstorm_retrieval_degraded session_id=%s collection_id=%s reason=%s",
                session_id,
                collection_id,
                exc,
            )
            retrieval_failures.append((query, collection_id, str(exc)))
            return []
        retrieval_cache_levels.extend(step.cache_level for step in execution.steps)
        retrieval_step_audits.extend(execution.steps)
        return list(execution.candidates)

    async def supplement_literature(
        queries: list[str], turn_number: int
    ) -> list[RetrievalCandidate]:
        try:
            async with httpx.AsyncClient(
                timeout=settings.literature_request_timeout_seconds,
                follow_redirects=True,
            ) as client:
                result = await ResearchLiteratureSupplementer(
                    session,
                    client,
                    settings,
                ).supplement_brainstorm(
                    project_id=history.session.project_id,
                    session_id=session_id,
                    session_number=history.session.session_number,
                    queries=queries,
                    turn_number=turn_number,
                )
            if not result.paper_ids:
                return []
            candidates = await retrieve_evidence(" ".join(queries), None)
            paper_ids = set(result.paper_ids)
            return [item for item in candidates if item.paper_id in paper_ids]
        except (LiteratureProviderError, httpx.HTTPError, ValueError):
            return []

    try:
        model_settings = await resolve_model_settings(session, settings)
        model_timeout = httpx.Timeout(600, connect=30)
        async with httpx.AsyncClient(timeout=model_timeout, follow_redirects=True) as client:
            model = build_model_provider(client, model_settings)
            result = await BrainstormOrchestrator(
                session,
                model=model,
                settings=model_settings,
                retrieve_evidence=retrieve_evidence,
                supplement_literature=supplement_literature,
                audit_session_factory=async_session_factory,
            ).run_turn(
                history.session,
                user_message=payload.content,
                regenerated_from_message_id=regenerated_from_message_id,
                regenerate_base_version_id=regenerate_base_version_id,
            )
        usage_service = UsageService(session)
        for cache_level in retrieval_cache_levels:
            await usage_service.record_retrieval(
                project_id=history.session.project_id,
                session_id=session_id,
                turn_number=result.turn_number,
                operation="brainstorm.retrieval",
                cache_level=cache_level,
            )
        route_memory = RetrievalRouteMemoryService(session)
        await route_memory.record_steps(
            project_id=history.session.project_id,
            workflow_id=workflow_id,
            workflow_kind="brainstorm",
            brainstorm_session_id=session_id,
            steps=retrieval_step_audits,
            scope={
                "project_id": str(history.session.project_id),
                "collection_id": (
                    str(history.session.collection_id)
                    if history.session.collection_id
                    else None
                ),
            },
        )
        for query, collection_id, reason in retrieval_failures:
            await route_memory.record(
                project_id=history.session.project_id,
                workflow_id=workflow_id,
                workflow_kind="brainstorm",
                brainstorm_session_id=session_id,
                round_number=result.turn_number,
                query=query,
                tool="local_retrieval",
                scope={
                    "project_id": str(history.session.project_id),
                    "collection_id": str(collection_id) if collection_id else None,
                },
                result_count=0,
                status="failed",
                failure_reason=reason,
            )
        await session.commit()
    except ModelConfigurationError as exc:
        try:
            await session.rollback()
        except Exception:
            pass
        await service.set_failed(session_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ModelResponseError, ValueError) as exc:
        try:
            await session.rollback()
        except Exception:
            pass
        await service.set_failed(session_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        try:
            await session.rollback()
        except Exception:
            pass
        await service.set_failed(session_id)
        logger.exception(
            "brainstorm_turn_failed session_id=%s error_type=%s",
            session_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=f"头脑风暴处理发生未预期错误：{str(exc)[:400]}；会话已恢复为可重试状态。",
        ) from exc

    refreshed = await session.get(BrainstormSession, session_id)
    session_usage = await _session_usage(session_id, UsageService(session))
    current_usage = next(
        (
            value
            for value in reversed(session_usage.turns)
            if value.message_id == result.message_id
        ),
        None,
    )
    return BrainstormTurnResponse(
        session_id=session_id,
        status=refreshed.status if refreshed else "awaiting_confirmation",
        version_id=result.version_id,
        version_number=result.version_number,
        response_markdown=result.coordinator.response_markdown,
        technical_route_mermaid=result.coordinator.technical_route_mermaid,
        confirmation_questions=result.coordinator.confirmation_questions,
        safety_flags=result.coordinator.safety_flags,
        evidence_ids=result.coordinator.evidence_ids,
        evidence_count=result.evidence_count,
        auto_ingested_paper_ids=list(result.auto_ingested_paper_ids),
        message_id=result.message_id,
        usage=(
            _usage_response(current_usage.model_dump())
            if current_usage is not None
            else _usage_response(usage_summary([]))
        ),
    )


@router.post(
    "/sessions/{session_id}/versions/{version_id}/restore",
    response_model=BrainstormRestoreResponse,
)
async def restore_brainstorm_version(
    session_id: UUID,
    version_id: UUID,
    session: SessionDependency,
) -> BrainstormRestoreResponse:
    service = BrainstormSessionService(session)
    try:
        history = await service.history(session_id)
        source = next(value for value in history.versions if value.id == version_id)
        restored = await service.restore_version(
            session_id=session_id,
            version_id=version_id,
        )
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Restore point does not exist") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BrainstormRestoreResponse(
        session_id=session_id,
        version_id=restored.id,
        version_number=restored.version_number,
        restored_from_version_id=source.id,
        restored_from_version_number=source.version_number,
    )


@router.post(
    "/sessions/{session_id}/regenerate",
    response_model=BrainstormTurnResponse,
)
async def regenerate_brainstorm_message(
    session_id: UUID,
    payload: BrainstormRegenerateRequest,
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> BrainstormTurnResponse:
    history = await BrainstormSessionService(session).history(session_id)
    target = next(
        (
            message
            for message in history.messages
            if message.id == payload.message_id and message.role == "assistant"
        ),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Assistant message does not exist")
    previous_user = next(
        (
            message
            for message in reversed(history.messages)
            if message.sequence_number < target.sequence_number and message.role == "user"
        ),
        None,
    )
    if previous_user is None:
        raise HTTPException(status_code=409, detail="No user message is available to regenerate")
    target_version_id = target.payload.get("version_id")
    target_version = next(
        (
            version
            for version in history.versions
            if str(version.id) == str(target_version_id)
        ),
        None,
    )
    if target_version is None:
        raise HTTPException(status_code=409, detail="Message has no restorable proposal version")
    return await send_brainstorm_message(
        session_id,
        BrainstormMessageRequest(content=previous_user.content),
        request,
        session,
        settings,
        regenerated_from_message_id=target.id,
        regenerate_base_version_id=target_version.parent_version_id,
    )


@router.post(
    "/sessions/{session_id}/plan/discover",
    response_model=BrainstormBackgroundJobResponse,
    status_code=202,
)
async def discover_plan_directions(
    session_id: UUID,
    payload: BrainstormPlanDiscoveryRequest,
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> BrainstormBackgroundJobResponse:
    try:
        history = await BrainstormSessionService(session).history(session_id)
        if history.session.workflow != "plan" or history.session.mode != "exploration":
            raise ValueError(
                "Research directions are only available in exploration Plan mode"
            )
        if not history.session.model_processing_allowed:
            raise ValueError("This session has not authorized model processing")
        if history.session.status == "processing":
            active_job = await session.scalar(
                select(Job)
                .where(
                    Job.kind == "brainstorm_plan_discovery",
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING]),
                    Job.payload["session_id"].as_string() == str(session_id),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            if active_job is not None:
                return _background_job_response(active_job)
            raise ValueError("The brainstorm session is already processing a task")
        job = Job(
            kind="brainstorm_plan_discovery",
            status=JobStatus.QUEUED,
            idempotency_key=f"brainstorm-plan-discovery:{session_id}:{uuid4().hex}",
            max_attempts=2,
            payload={
                "session_id": str(session_id),
                "request": payload.model_dump(mode="json"),
            },
        )
        session.add(job)
        await session.flush()
        history.session.status = "processing"
        history.session.phase = "discovering_directions"
        _set_plan_background_state(history.session, job)
        await session.commit()
        task_manager = cast(InProcessTaskManager, request.app.state.background_tasks)
        task_manager.start(
            _run_plan_discovery_job(
                job.id,
                cache=cast(ThreeLevelCache, request.app.state.cache),
                settings=settings,
            )
        )
        return _background_job_response(job)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/sessions/{session_id}/background-job",
    response_model=BrainstormBackgroundJobResponse | None,
)
async def get_brainstorm_background_job(
    session_id: UUID,
    session: SessionDependency,
) -> BrainstormBackgroundJobResponse | None:
    history = await BrainstormSessionService(session).history(session_id)
    job = await session.scalar(
        select(Job)
        .where(
            Job.kind.in_(["brainstorm_plan_discovery", "brainstorm_plan_generation"]),
            Job.payload["session_id"].as_string() == str(history.session.id),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return _background_job_response(job) if job is not None else None


@router.put(
    "/sessions/{session_id}/plan/preferences",
    response_model=BrainstormPlanSnapshotResponse,
)
async def save_plan_preferences(
    session_id: UUID,
    payload: BrainstormPreferenceRequest,
    session: SessionDependency,
) -> BrainstormPlanSnapshotResponse:
    try:
        history = await BrainstormSessionService(session).history(session_id)
        profile = PreferenceProfile(
            objective_type=payload.objective_type,
            model_system=payload.model_system,
            endpoint_priority=payload.endpoint_priority,
            budget_level=payload.budget_level,
            timeline_weeks=payload.timeline_weeks,
            sample_availability=payload.sample_availability,
            risk_tolerance=payload.risk_tolerance,
            must_have_constraints=payload.must_have_constraints,
            free_text=payload.free_text,
        )
        await BrainstormPlanService(session).save_preferences(
            brainstorm=history.session,
            selected_direction_id=payload.selected_direction_id,
            profile=profile,
            answers=payload.answers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _plan_snapshot_response(history.session)


@router.post(
    "/sessions/{session_id}/plan/prefetch",
    response_model=BrainstormPrefetchResponse,
)
async def prefetch_plan_direction(
    session_id: UUID,
    payload: BrainstormPrefetchRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> BrainstormPrefetchResponse:
    try:
        history = await BrainstormSessionService(session).history(session_id)
        if history.session.workflow != "plan" or history.session.mode != "exploration":
            raise ValueError(
                "Direction prefetching is only available in exploration Plan mode"
            )
        result = await BrainstormPlanService(session).prefetch_direction_literature(
            brainstorm=history.session,
            selected_direction_id=payload.selected_direction_id,
            settings=settings,
        )
        return BrainstormPrefetchResponse(
            session_id=session_id,
            direction_id=payload.selected_direction_id,
            status=result["status"],
            prefetched_paper_ids=result.get("prefetched_paper_ids", []),
            error=result.get("error"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/sessions/{session_id}/plan/generate",
    response_model=BrainstormBackgroundJobResponse,
    status_code=202,
)
async def generate_plan_proposal(
    session_id: UUID,
    payload: BrainstormPreferenceRequest,
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> BrainstormBackgroundJobResponse:
    try:
        history = await BrainstormSessionService(session).history(session_id)
        if history.session.status == "processing":
            active_job = await session.scalar(
                select(Job)
                .where(
                    Job.kind == "brainstorm_plan_generation",
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING]),
                    Job.payload["session_id"].as_string() == str(session_id),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            if active_job is not None:
                return _background_job_response(active_job)
            raise ValueError("The brainstorm session is already processing a task")
        profile = PreferenceProfile(
            objective_type=payload.objective_type,
            model_system=payload.model_system,
            endpoint_priority=payload.endpoint_priority,
            budget_level=payload.budget_level,
            timeline_weeks=payload.timeline_weeks,
            sample_availability=payload.sample_availability,
            risk_tolerance=payload.risk_tolerance,
            must_have_constraints=payload.must_have_constraints,
            free_text=payload.free_text,
        )
        await BrainstormPlanService(session).save_preferences(
            brainstorm=history.session,
            selected_direction_id=payload.selected_direction_id,
            profile=profile,
            answers=payload.answers,
        )
        BrainstormPlanService.build_generation_request(history.session)
        job = Job(
            kind="brainstorm_plan_generation",
            status=JobStatus.QUEUED,
            idempotency_key=f"brainstorm-plan-generation:{session_id}:{uuid4().hex}",
            max_attempts=2,
            payload={
                "session_id": str(session_id),
                "request": payload.model_dump(mode="json"),
            },
        )
        session.add(job)
        await session.flush()
        history.session.status = "processing"
        history.session.phase = "generating_proposal"
        _set_plan_background_state(history.session, job)
        await session.commit()
        task_manager = cast(InProcessTaskManager, request.app.state.background_tasks)
        task_manager.start(
            _run_plan_generation_job(
                job.id,
                cache=cast(ThreeLevelCache, request.app.state.cache),
                settings=settings,
            )
        )
        return _background_job_response(job)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/confirm", response_model=BrainstormConfirmResponse)
async def confirm_brainstorm_session(
    session_id: UUID,
    session: SessionDependency,
) -> BrainstormConfirmResponse:
    try:
        final = await BrainstormSessionService(session).finalize(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BrainstormConfirmResponse(
        session_id=session_id,
        status="finalized",
        version_id=final.id,
        version_number=final.version_number,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_brainstorm_session(
    session_id: UUID,
    session: SessionDependency,
) -> Response:
    try:
        await BrainstormSessionService(session).soft_delete(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post(
    "/sessions/{session_id}/messages/{message_id}/dispatch-to-workbench",
    response_model=BrainstormTaskDispatchResponse,
)
async def dispatch_brainstorm_message_to_workbench(
    session_id: UUID,
    message_id: UUID,
    payload: BrainstormTaskDispatchRequest,
    session: SessionDependency,
) -> BrainstormTaskDispatchResponse:
    try:
        result = await BrainstormTaskDispatchService(session).dispatch(
            session_id=session_id,
            message_id=message_id,
            work_date=payload.work_date,
            priority=payload.priority,
        )
        await session.commit()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return BrainstormTaskDispatchResponse(
        session_id=result.session_id,
        message_id=result.message_id,
        sections_detected=result.sections_detected,
        created_count=result.created_count,
        skipped_existing=result.skipped_existing,
        tasks=[_workbench_task_response(task) for task in result.tasks],
    )
