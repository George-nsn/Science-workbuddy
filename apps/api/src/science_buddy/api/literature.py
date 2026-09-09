from typing import Annotated
from uuid import UUID, uuid4

import httpx
from arq.connections import RedisSettings, create_pool
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.schemas import (
    BulkPaperDeleteRequest,
    BulkPaperDeleteResponse,
    ImportedPaper,
    IndexProjectRequest,
    IndexProjectResponse,
    JournalMetricResponse,
    LibraryPaper,
    LibraryResponse,
    LiteratureArchiveCreateRequest,
    LiteratureArchiveResponse,
    LiteratureArchivesResponse,
    LiteratureImportRequest,
    LiteratureImportResponse,
    LiteratureMetadataEnrichItem,
    LiteratureMetadataEnrichRequest,
    LiteratureMetadataEnrichResponse,
    LiteratureSearchItem,
    LiteratureSearchRequest,
    LiteratureSearchResponse,
    PaperTagResponse,
    PaperTagsResponse,
    PaperTagsUpdateRequest,
    QueryExpansionItem,
    ResearchRoutingDecisionResponse,
    RetrievalItem,
    RetrievalQueryPlanResponse,
    RetrievalRequest,
    RetrievalRerankerSummary,
    RetrievalResponse,
    RetrievalRouteSummary,
    RetrievalStepAuditResponse,
    RetrievalTraceItem,
    TagFilterResponse,
)
from science_buddy.config import Settings, get_settings
from science_buddy.domain.providers import LiteratureProvider
from science_buddy.infrastructure.database import async_session_factory, get_session
from science_buddy.infrastructure.models import (
    ArchivePaper,
    JournalMetric,
    LiteratureArchive,
    Paper,
    PaperTag,
    Project,
    ProjectPaper,
    RagCollection,
    SearchRun,
    Tag,
)
from science_buddy.services.adaptive_retrieval import (
    AdaptiveRetrievalService,
    retrieval_mode,
)
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.library_management import (
    LiteratureArchiveService,
    ProjectPaperDeletionService,
)
from science_buddy.services.literature import (
    CrossrefProvider,
    EuropePmcProvider,
    OpenAlexProvider,
    PubMedProvider,
)
from science_buddy.services.literature.common import LiteratureProviderError
from science_buddy.services.literature.discovery import LiteratureDiscoveryService
from science_buddy.services.literature.ingestion import LiteratureIngestionService
from science_buddy.services.query_planning import DeterministicQueryPlanner
from science_buddy.services.reranking import get_reranker_service
from science_buddy.services.research_routing import (
    DeterministicResearchRouter,
    retrieval_steps_payload,
    routing_audit_payload,
)
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever
from science_buddy.services.scholarly_metadata import ScholarlyMetadataService
from science_buddy.services.tags import TagService

router = APIRouter(tags=["literature"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def _build_discovery(client: httpx.AsyncClient, settings: Settings) -> LiteratureDiscoveryService:
    providers: list[LiteratureProvider] = [
        PubMedProvider(
            client,
            base_url=settings.ncbi_base_url,
            tool=settings.ncbi_tool,
            email=settings.ncbi_email,
            api_key=(
                settings.ncbi_api_key.get_secret_value() if settings.ncbi_api_key else None
            ),
        ),
        EuropePmcProvider(client, base_url=settings.europe_pmc_base_url),
        OpenAlexProvider(
            client,
            base_url=settings.openalex_base_url,
            api_key=(
                settings.openalex_api_key.get_secret_value()
                if settings.openalex_api_key
                else None
            ),
        ),
        CrossrefProvider(client, base_url=settings.crossref_base_url),
    ]
    return LiteratureDiscoveryService(providers)


def _http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.literature_request_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": f"{settings.ncbi_tool}/0.1 (local research assistant)"},
    )


@router.post("/literature/search", response_model=LiteratureSearchResponse)
async def search_literature(
    request: LiteratureSearchRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> LiteratureSearchResponse:
    try:
        async with _http_client(settings) as client:
            discovery = _build_discovery(client, settings)
            candidates = await discovery.search(
                request.query,
                provider_names=request.providers,
                limit=request.limit,
                tolerate_failures=True,
            )
    except LiteratureProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run = SearchRun(
        project_id=request.project_id,
        query=request.query,
        providers=list(request.providers),
        filters={"limit": request.limit},
        result_snapshot=[candidate.model_dump(mode="json") for candidate in candidates],
        total_results=len(candidates),
    )
    session.add(run)
    await session.commit()
    return LiteratureSearchResponse(
        search_run_id=run.id,
        query=request.query,
        items=[
            LiteratureSearchItem.model_validate(candidate.model_dump())
            for candidate in candidates
        ],
    )


@router.post("/literature/import", response_model=LiteratureImportResponse)
async def import_literature(
    request: LiteratureImportRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> LiteratureImportResponse:
    try:
        async with _http_client(settings) as client:
            discovery = _build_discovery(client, settings)
            requested_providers = {reference.provider for reference in request.references}
            unknown = requested_providers - discovery.provider_names
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported literature providers: {', '.join(sorted(unknown))}",
                )
            records = await discovery.fetch_references(request.references)
        ingestor = LiteratureIngestionService(
            session,
            default_project_name=settings.default_project_name,
        )
        results = [
            await ingestor.ingest(record, project_id=request.project_id) for record in records
        ]
        await session.commit()
    except LiteratureProviderError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not results:
        raise HTTPException(status_code=404, detail="No literature records were resolved")
    unique: dict[UUID, ImportedPaper] = {}
    for result in results:
        previous = unique.get(result.paper_id)
        unique[result.paper_id] = ImportedPaper(
            paper_id=result.paper_id,
            created=result.created if previous is None else previous.created,
            chunks_created=result.chunks_created + (previous.chunks_created if previous else 0),
        )
    return LiteratureImportResponse(
        project_id=results[0].project_id,
        papers=list(unique.values()),
    )


@router.get("/library", response_model=LibraryResponse)
async def get_library(
    session: SessionDependency,
    settings: SettingsDependency,
    project_id: UUID | None = None,
    q: str | None = None,
    tag_ids: Annotated[list[UUID] | None, Query()] = None,
    archive_id: UUID | None = None,
) -> LibraryResponse:
    ingestor = LiteratureIngestionService(
        session,
        default_project_name=settings.default_project_name,
    )
    try:
        project = await ingestor.ensure_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await TagService(session).ensure_project_auto_tags(project.id)
    await session.flush()
    statement = (
        select(Paper, ProjectPaper.tags_manually_curated, JournalMetric)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .outerjoin(JournalMetric, JournalMetric.id == Paper.journal_metric_id)
        .where(ProjectPaper.project_id == project.id)
    )
    if q and q.strip():
        pattern = f"%{q.strip().casefold()}%"
        statement = statement.where(
            or_(
                func.lower(Paper.title).like(pattern),
                func.lower(func.coalesce(Paper.abstract, "")).like(pattern),
                func.lower(func.coalesce(Paper.journal, "")).like(pattern),
                func.lower(func.coalesce(Paper.pmid, "")).like(pattern),
                func.lower(func.coalesce(Paper.pmcid, "")).like(pattern),
                func.lower(func.coalesce(Paper.doi_normalized, "")).like(pattern),
            )
        )
    selected_tag_ids = set(tag_ids or [])
    if selected_tag_ids:
        tagged_papers = (
            select(PaperTag.paper_id)
            .where(
                PaperTag.project_id == project.id,
                PaperTag.tag_id.in_(selected_tag_ids),
            )
            .group_by(PaperTag.paper_id)
            .having(func.count(func.distinct(PaperTag.tag_id)) == len(selected_tag_ids))
        )
        statement = statement.where(Paper.id.in_(tagged_papers))
    if archive_id:
        archive = await session.get(LiteratureArchive, archive_id)
        if archive is None or archive.project_id != project.id:
            raise HTTPException(status_code=404, detail="Literature archive not found")
        statement = statement.where(
            Paper.id.in_(
                select(ArchivePaper.paper_id).where(
                    ArchivePaper.archive_id == archive_id
                )
            )
        )
    paper_rows = (
        await session.execute(
            statement.order_by(Paper.publication_year.desc().nullslast(), Paper.title)
        )
    ).all()
    paper_tags: dict[UUID, list[PaperTagResponse]] = {}
    tag_rows = (
        await session.execute(
            select(PaperTag.paper_id, Tag, PaperTag.origin)
            .join(Tag, Tag.id == PaperTag.tag_id)
            .where(PaperTag.project_id == project.id)
            .order_by(Tag.name)
        )
    ).all()
    for tagged_paper_id, tag, origin in tag_rows:
        paper_tags.setdefault(tagged_paper_id, []).append(
            PaperTagResponse(id=tag.id, name=tag.name, kind=tag.kind, origin=origin)
        )
    available_tags = await TagService(session).project_tag_counts(project.id)
    await session.commit()
    return LibraryResponse(
        project_id=project.id,
        project_name=project.name,
        papers=[
            LibraryPaper(
                id=paper.id,
                title=paper.title,
                abstract=paper.abstract,
                abstract_source=paper.abstract_source,
                pmid=paper.pmid,
                pmcid=paper.pmcid,
                doi=paper.doi_normalized,
                journal=paper.journal,
                publication_year=paper.publication_year,
                publication_types=paper.publication_types,
                is_open_access=paper.is_open_access,
                open_access_status=paper.open_access_status,
                open_access_url=paper.open_access_url,
                is_retracted=paper.is_retracted,
                retraction_status=paper.retraction_status,
                citation_count=paper.citation_count,
                influential_citation_count=paper.influential_citation_count,
                quality_signals=paper.quality_signals,
                journal_metric=(
                    JournalMetricResponse(
                        source="openalex",
                        two_year_mean_citedness=journal_metric.two_year_mean_citedness,
                        open_quartile=journal_metric.open_quartile,
                        percentile=journal_metric.percentile,
                        importance_score=journal_metric.importance_score,
                        quartile_basis=journal_metric.quartile_basis,
                        updated_date=journal_metric.metric_updated_date,
                        note=journal_metric.metric_note,
                    )
                    if journal_metric is not None
                    else None
                ),
                metadata_sources=paper.metadata_sources,
                tags=paper_tags.get(paper.id, []),
                tags_manually_curated=manually_curated,
            )
            for paper, manually_curated, journal_metric in paper_rows
        ],
        available_tags=[
            TagFilterResponse(
                id=tag.id,
                name=tag.name,
                kind=tag.kind,
                paper_count=count,
            )
            for tag, count in available_tags
        ],
    )


@router.post("/library/archives", response_model=LiteratureArchiveResponse)
async def archive_papers(
    payload: LiteratureArchiveCreateRequest,
    session: SessionDependency,
) -> LiteratureArchiveResponse:
    try:
        summary = await LiteratureArchiveService(session).add_papers(
            project_id=payload.project_id,
            paper_ids=payload.paper_ids,
            name=payload.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LiteratureArchiveResponse(
        id=summary.archive.id,
        project_id=summary.archive.project_id,
        name=summary.archive.name,
        paper_count=summary.paper_count,
        origin=summary.archive.origin,
        created_at=summary.archive.created_at.isoformat(),
    )


@router.get("/library/archives", response_model=LiteratureArchivesResponse)
async def list_literature_archives(
    project_id: UUID,
    session: SessionDependency,
) -> LiteratureArchivesResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    values = await LiteratureArchiveService(session).summaries(project_id)
    return LiteratureArchivesResponse(
        project_id=project_id,
        archives=[
            LiteratureArchiveResponse(
                id=value.archive.id,
                project_id=value.archive.project_id,
                name=value.archive.name,
                paper_count=value.paper_count,
                origin=value.archive.origin,
                created_at=value.archive.created_at.isoformat(),
            )
            for value in values
        ],
    )


@router.post(
    "/library/metadata/enrich",
    response_model=LiteratureMetadataEnrichResponse,
)
async def enrich_library_metadata(
    payload: LiteratureMetadataEnrichRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> LiteratureMetadataEnrichResponse:
    if await session.get(Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    async with _http_client(settings) as client:
        results = await ScholarlyMetadataService(session, client, settings).enrich_project(
            payload.project_id,
            payload.paper_ids,
        )
    enriched = sum(bool(item.sources) for item in results)
    return LiteratureMetadataEnrichResponse(
        project_id=payload.project_id,
        processed=len(results),
        enriched=enriched,
        failed=len(results) - enriched,
        references_linked=sum(item.references_linked for item in results),
        items=[
            LiteratureMetadataEnrichItem(
                paper_id=item.paper_id,
                sources=list(item.sources),
                references_linked=item.references_linked,
                errors=list(item.errors),
            )
            for item in results
        ],
    )


@router.post("/library/papers/bulk-delete", response_model=BulkPaperDeleteResponse)
async def delete_library_papers(
    payload: BulkPaperDeleteRequest,
    request: Request,
    session: SessionDependency,
) -> BulkPaperDeleteResponse:
    try:
        result = await ProjectPaperDeletionService(session).delete(
            project_id=payload.project_id,
            paper_ids=payload.paper_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.cache.invalidate_namespace(
        f"retrieval:{payload.project_id}"
    )
    return BulkPaperDeleteResponse(
        project_id=payload.project_id,
        requested=result.requested,
        removed_from_project=result.removed_from_project,
        deleted_globally=result.deleted_globally,
        collections_updated=result.collections_updated,
        collections_deleted=result.collections_deleted,
    )


@router.put("/library/papers/{paper_id}/tags", response_model=PaperTagsResponse)
async def update_paper_tags(
    paper_id: UUID,
    payload: PaperTagsUpdateRequest,
    session: SessionDependency,
) -> PaperTagsResponse:
    try:
        tags = await TagService(session).replace_manual_tags(
            payload.project_id, paper_id, payload.labels
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaperTagsResponse(
        project_id=payload.project_id,
        paper_id=paper_id,
        manually_curated=True,
        tags=[
            PaperTagResponse(id=value.id, name=value.name, kind=value.kind, origin="manual")
            for value in tags
        ],
    )


@router.post("/library/papers/{paper_id}/tags/reset", response_model=PaperTagsResponse)
async def reset_paper_tags(
    paper_id: UUID,
    project_id: UUID,
    session: SessionDependency,
) -> PaperTagsResponse:
    try:
        tags = await TagService(session).reset_to_auto(project_id, paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaperTagsResponse(
        project_id=project_id,
        paper_id=paper_id,
        manually_curated=False,
        tags=[
            PaperTagResponse(id=value.id, name=value.name, kind=value.kind, origin="auto")
            for value in tags
        ],
    )


@router.post("/retrieval/search", response_model=RetrievalResponse)
async def retrieve_evidence(
    request: RetrievalRequest,
    http_request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> RetrievalResponse:
    project = await session.get(Project, request.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if request.collection_id:
        collection = await session.get(RagCollection, request.collection_id)
        if collection is None or collection.project_id != request.project_id:
            raise HTTPException(status_code=404, detail="RAG collection not found in project")
    workflow_id = request.workflow_id or uuid4()
    query_plan = DeterministicQueryPlanner().plan(request.query)
    routing = DeterministicResearchRouter().route(
        request.query,
        preference=request.strategy,
        max_subqueries=request.max_subqueries,
        max_followup_rounds=request.max_followup_rounds,
        identifier=query_plan.is_identifier_lookup,
    )
    token_service = EvidenceTokenService(settings.evidence_signing_key.get_secret_value())
    retrieval_config = RetrievalConfig.from_settings(settings)
    try:
        execution = await AdaptiveRetrievalService(
            cache=http_request.app.state.cache,
            token_service=token_service,
            workflow_id=workflow_id,
            project_id=request.project_id,
            collection_id=request.collection_id,
            project_revision=project.retrieval_revision,
            config=retrieval_config,
            retriever_factory=lambda: SQLiteHybridRetriever(
                session,
                token_service,
                workflow_id=workflow_id,
                config=retrieval_config,
                embedding_service=get_embedding_service(),
                session_factory=async_session_factory,
                collection_id=request.collection_id,
                reranker=(
                    get_reranker_service(retrieval_config.reranker_model)
                    if retrieval_config.reranker_enabled
                    else None
                ),
            ),
        ).execute(routing, limit=request.limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    candidates = list(execution.candidates)
    reports = [step.report for step in execution.steps if step.report is not None]
    route_values = [
        (step.subquery_id, route)
        for step in execution.steps
        if step.report is not None
        for route in step.report["routes"]
    ]
    rerankers = [report["reranker"] for report in reports]
    cache_levels = [step.cache_level for step in execution.steps]
    cache_level = "miss" if "miss" in cache_levels else cache_levels[0]
    reranker_errors = [str(value["error"]) for value in rerankers if value.get("error")]
    return RetrievalResponse(
        workflow_id=workflow_id,
        retrieval_mode=retrieval_mode(
            candidates,
            identifier=query_plan.is_identifier_lookup,
        ),
        retrieval_version=retrieval_config.version,
        query_plan=RetrievalQueryPlanResponse(
            language=query_plan.language,
            original_query=query_plan.original_query,
            english_query=query_plan.english_query,
            expansions=[
                QueryExpansionItem(source=item.source, target=item.target)
                for item in query_plan.expansions
            ],
        ),
        routes=[
            RetrievalRouteSummary(
                route=(
                    str(route["route"])
                    if len(execution.steps) == 1
                    else f"{subquery_id}:{route['route']}"
                ),
                candidates=int(route["candidates"]),
                elapsed_ms=float(route["elapsed_ms"]),
                error=str(route["error"]) if route.get("error") else None,
                metrics=dict(route.get("metrics", {})),
            )
            for subquery_id, route in route_values
        ],
        reranker=RetrievalRerankerSummary(
            enabled=any(bool(value["enabled"]) for value in rerankers),
            applied=any(bool(value["applied"]) for value in rerankers),
            model=next((str(value["model"]) for value in rerankers if value.get("model")), None),
            candidates=sum(int(value["candidates"]) for value in rerankers),
            elapsed_ms=sum(float(value["elapsed_ms"]) for value in rerankers),
            error="; ".join(reranker_errors) or None,
        ),
        excluded_retracted=sum(int(report["excluded_retracted"]) for report in reports),
        elapsed_ms=sum(float(report["elapsed_ms"]) for report in reports),
        cache_level=cache_level,
        routing=ResearchRoutingDecisionResponse.model_validate(
            routing_audit_payload(routing)
        ),
        retrieval_steps=[
            RetrievalStepAuditResponse.model_validate(value)
            for value in retrieval_steps_payload(execution.steps)
        ],
        items=[
            RetrievalItem(
                chunk_id=candidate.chunk_id,
                evidence_id=candidate.evidence_id,
                text=candidate.text,
                score=candidate.score,
                source_locator=candidate.source_locator,
                paper_id=candidate.paper_id,
                role=candidate.role,
                anchor_chunk_id=candidate.anchor_chunk_id,
                traces=[
                    RetrievalTraceItem(
                        route=trace.route,
                        rank=trace.rank,
                        raw_score=trace.raw_score,
                        weighted_rrf=trace.weighted_rrf,
                    )
                    for trace in candidate.traces
                ],
            )
            for candidate in candidates
        ],
    )


@router.post("/retrieval/index", response_model=IndexProjectResponse)
async def enqueue_project_index(
    request: IndexProjectRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> IndexProjectResponse:
    if await session.get(Project, request.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        job = await pool.enqueue_job("embed_project_chunks", str(request.project_id))
    finally:
        await pool.aclose()
    if job is None:
        raise HTTPException(status_code=503, detail="Could not enqueue embedding job")
    return IndexProjectResponse(project_id=request.project_id, job_id=job.job_id)
