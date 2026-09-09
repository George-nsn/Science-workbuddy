import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.api.literature import SettingsDependency, _http_client
from science_buddy.api.schemas import (
    DocumentBatchImportResponse,
    DocumentBatchItemResponse,
    DocumentImportResponse,
    FullTextImportRequest,
    LiteratureArchiveResponse,
    PdfEnrichmentRequest,
    PdfEnrichmentResponse,
)
from science_buddy.config import Settings
from science_buddy.domain.enums import AssetSource, EvidenceDepth
from science_buddy.infrastructure.database import get_session
from science_buddy.infrastructure.models import DocumentAsset, Paper, Project, ProjectPaper
from science_buddy.services.document_ingestion import (
    DocumentIngestionService,
    ensure_paper_in_project,
)
from science_buddy.services.documents import (
    DocumentParseError,
    EuropePmcXmlParser,
    build_pdf_parser,
)
from science_buddy.services.library_management import LiteratureArchiveService
from science_buddy.services.literature import EuropePmcProvider
from science_buddy.services.literature.common import LiteratureProviderError
from science_buddy.services.literature.ingestion import LiteratureIngestionService
from science_buddy.services.pdf_enrichment import (
    enrich_project_pdfs,
    extract_pdf_enrichment,
)
from science_buddy.services.tags import TagService

router = APIRouter(prefix="/documents", tags=["documents"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
logger = logging.getLogger(__name__)


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while data := await upload.read(1024 * 1024):
        size += len(data)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail="PDF exceeds the configured size limit")
        chunks.append(data)
    return b"".join(chunks)


async def _ingest_pdf_upload(
    *,
    session: AsyncSession,
    settings: Settings,
    file: UploadFile,
    title: str | None,
    project_id: UUID | None,
    paper_id: UUID | None,
    cloud_processing_allowed: bool,
) -> DocumentImportResponse:
    payload = await _read_limited(file, settings.max_upload_bytes)
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Only PDF files are supported")

    project_service = LiteratureIngestionService(
        session,
        default_project_name=settings.default_project_name,
    )
    project = await project_service.ensure_project(project_id)
    if not paper_id:
        content_hash = hashlib.sha256(payload).hexdigest()
        existing = (
            await session.execute(
                select(DocumentAsset, Paper)
                .join(Paper, Paper.id == DocumentAsset.paper_id)
                .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
                .where(
                    ProjectPaper.project_id == project.id,
                    DocumentAsset.content_hash == content_hash,
                )
                .order_by(DocumentAsset.created_at)
                .limit(1)
            )
        ).first()
        if existing is not None:
            existing_asset, existing_paper = existing
            return DocumentImportResponse(
                project_id=project.id,
                paper_id=existing_paper.id,
                asset_id=existing_asset.id,
                evidence_depth=existing_asset.evidence_depth.value,
                chunks_created=0,
                duplicate=True,
            )
    if paper_id:
        try:
            paper = await ensure_paper_in_project(
                session,
                project_id=project.id,
                paper_id=paper_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        filename_title = Path(file.filename or "Uploaded research paper").stem
        paper_title = (title or filename_title).strip()
        if not paper_title:
            raise HTTPException(status_code=422, detail="A title is required")
        paper = Paper(
            title=paper_title,
            authors=[],
            publication_types=[],
        )
        session.add(paper)
        await session.flush()
        session.add(ProjectPaper(project_id=project.id, paper_id=paper.id))

    settings.upload_directory.mkdir(parents=True, exist_ok=True)
    target = settings.upload_directory / f"{uuid4().hex}.pdf"
    target.write_bytes(payload)
    try:
        parser = build_pdf_parser(
            backend=settings.pdf_parser_backend,
            grobid_base_url=settings.grobid_base_url,
            timeout_seconds=settings.document_parser_timeout_seconds,
        )
        sections = await asyncio.to_thread(parser.parse, target)
        enrichment = extract_pdf_enrichment(sections)
        if not paper.abstract:
            paper.abstract = enrichment.summary
            paper.abstract_source = enrichment.summary_kind
        asset, chunks_created = await DocumentIngestionService(session).ingest_sections(
            paper=paper,
            source=AssetSource.USER_PDF,
            evidence_depth=EvidenceDepth.USER_PDF,
            content=payload,
            sections=sections,
            media_type="application/pdf",
            storage_path=target,
            parser_version=parser.parser_version,
            extraction_metadata=parser.extraction_metadata,
            cloud_processing_allowed=cloud_processing_allowed,
        )
        tag_service = TagService(session)
        await tag_service.auto_tag_paper(project.id, paper.id)
        await tag_service.add_auto_topic_tags(
            project.id,
            paper.id,
            list(enrichment.keywords),
        )
        project.retrieval_revision += 1
        await session.commit()
    except DocumentParseError as exc:
        await session.rollback()
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        target.unlink(missing_ok=True)
        logger.exception("pdf_import_failed filename=%s", file.filename)
        raise HTTPException(
            status_code=500,
            detail="PDF import failed unexpectedly; check the API log and retry",
        ) from exc

    duplicate = chunks_created == 0
    if duplicate and asset.storage_path and Path(asset.storage_path) != target:
        target.unlink(missing_ok=True)
    return DocumentImportResponse(
        project_id=project.id,
        paper_id=paper.id,
        asset_id=asset.id,
        evidence_depth=asset.evidence_depth.value,
        chunks_created=chunks_created,
        duplicate=duplicate,
    )


@router.post("/upload", response_model=DocumentImportResponse)
async def upload_pdf(
    session: SessionDependency,
    settings: SettingsDependency,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    project_id: Annotated[UUID | None, Form()] = None,
    paper_id: Annotated[UUID | None, Form()] = None,
    cloud_processing_allowed: Annotated[bool, Form()] = False,
) -> DocumentImportResponse:
    return await _ingest_pdf_upload(
        session=session,
        settings=settings,
        file=file,
        title=title,
        project_id=project_id,
        paper_id=paper_id,
        cloud_processing_allowed=cloud_processing_allowed,
    )


@router.post("/upload-batch", response_model=DocumentBatchImportResponse)
async def upload_pdf_batch(
    session: SessionDependency,
    settings: SettingsDependency,
    files: Annotated[list[UploadFile], File()],
    project_id: Annotated[UUID | None, Form()] = None,
    relative_paths: Annotated[list[str] | None, Form()] = None,
    cloud_processing_allowed: Annotated[bool, Form()] = False,
) -> DocumentBatchImportResponse:
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one PDF")
    if len(files) > settings.max_batch_upload_files:
        raise HTTPException(
            status_code=413,
            detail=(
                f"A batch can contain at most {settings.max_batch_upload_files} PDF files"
            ),
        )
    paths = relative_paths or []
    items: list[DocumentBatchItemResponse] = []
    for index, file in enumerate(files):
        filename = Path(file.filename or f"document-{index + 1}.pdf").name
        relative_path = paths[index] if index < len(paths) else filename
        try:
            result = await _ingest_pdf_upload(
                session=session,
                settings=settings,
                file=file,
                title=Path(filename).stem,
                project_id=project_id,
                paper_id=None,
                cloud_processing_allowed=cloud_processing_allowed,
            )
            items.append(
                DocumentBatchItemResponse(
                    filename=filename,
                    relative_path=relative_path,
                    status="imported",
                    result=result,
                )
            )
        except HTTPException as exc:
            await session.rollback()
            # Per-file isolation: one failing file (including server-side 5xx
            # errors from that single ingestion) is reported in the batch
            # result and never aborts the remaining files.
            items.append(
                DocumentBatchItemResponse(
                    filename=filename,
                    relative_path=relative_path,
                    status="failed",
                    error=str(exc.detail),
                )
            )
    imported = sum(item.status == "imported" for item in items)
    duplicates = sum(
        bool(item.result and item.result.duplicate)
        for item in items
        if item.status == "imported"
    )
    successful_results = [
        item.result
        for item in items
        if item.status == "imported" and item.result is not None
    ]
    paper_ids = list(
        dict.fromkeys(
            result.paper_id
            for result in successful_results
        )
    )
    archive_response = None
    if successful_results:
        archive_name = f"新增档案 · {datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}"
        archive_summary = await LiteratureArchiveService(session).add_papers(
            project_id=successful_results[0].project_id,
            paper_ids=paper_ids,
            name=archive_name,
            origin="upload_batch",
            reuse_name=False,
        )
        archive_response = LiteratureArchiveResponse(
            id=archive_summary.archive.id,
            project_id=archive_summary.archive.project_id,
            name=archive_summary.archive.name,
            paper_count=archive_summary.paper_count,
            origin="upload_batch",
            created_at=archive_summary.archive.created_at.isoformat(),
        )
    return DocumentBatchImportResponse(
        total=len(items),
        imported=imported,
        failed=len(items) - imported,
        duplicates=duplicates,
        paper_ids=paper_ids,
        archive=archive_response,
        items=items,
    )


@router.post("/enrich-pdfs", response_model=PdfEnrichmentResponse)
async def enrich_uploaded_pdfs(
    request: PdfEnrichmentRequest,
    session: SessionDependency,
) -> PdfEnrichmentResponse:
    if await session.get(Project, request.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    result = await enrich_project_pdfs(session, project_id=request.project_id)
    return PdfEnrichmentResponse(
        project_id=request.project_id,
        processed=result.processed,
        summaries_updated=result.summaries_updated,
        tagged=result.tagged,
        failed=result.failed,
    )


@router.post("/import-europe-pmc", response_model=DocumentImportResponse)
async def import_europe_pmc_full_text(
    request: FullTextImportRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> DocumentImportResponse:
    try:
        paper = await ensure_paper_in_project(
            session,
            project_id=request.project_id,
            paper_id=request.paper_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not paper.pmcid:
        raise HTTPException(status_code=422, detail="The paper has no PMCID")

    try:
        async with _http_client(settings) as client:
            provider = EuropePmcProvider(client, base_url=settings.europe_pmc_base_url)
            payload = await provider.fetch_open_full_text_xml(paper.pmcid)
        sections = EuropePmcXmlParser().parse(payload)
        asset, chunks_created = await DocumentIngestionService(session).ingest_sections(
            paper=paper,
            source=AssetSource.EUROPE_PMC,
            evidence_depth=EvidenceDepth.FULLTEXT_XML,
            content=payload,
            sections=sections,
            media_type="application/xml",
            access_url=f"https://europepmc.org/articles/{paper.pmcid}",
            parser_version="europe-pmc-jats-v1",
        )
        project = await session.get(Project, request.project_id)
        if project is not None:
            await TagService(session).auto_tag_paper(project.id, paper.id)
            project.retrieval_revision += 1
        await session.commit()
    except (LiteratureProviderError, DocumentParseError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail="Europe PMC full-text request failed") from exc

    return DocumentImportResponse(
        project_id=request.project_id,
        paper_id=paper.id,
        asset_id=asset.id,
        evidence_depth=asset.evidence_depth.value,
        chunks_created=chunks_created,
        duplicate=chunks_created == 0,
    )
