import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.domain.contracts import ChunkingService, SourceSegment
from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.domain.providers import LiteratureRecord
from science_buddy.infrastructure.models import (
    Chunk,
    DocumentAsset,
    MeSHTerm,
    Paper,
    PaperIdentifier,
    PaperMeSH,
    Project,
    ProjectPaper,
    Section,
)
from science_buddy.services.chunking import StructureAwareChunkingService
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.literature.common import normalize_doi, normalize_identifier
from science_buddy.services.tags import TagService


@dataclass(frozen=True, slots=True)
class IngestionResult:
    project_id: UUID
    paper_id: UUID
    created: bool
    chunks_created: int


class LiteratureIngestionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        default_project_name: str,
        chunker: ChunkingService | None = None,
    ) -> None:
        self._session = session
        self._default_project_name = default_project_name
        self._chunker = chunker or StructureAwareChunkingService(
            semantic_embedder=get_embedding_service()
        )

    async def ensure_project(self, project_id: UUID | None) -> Project:
        if project_id:
            project = await self._session.get(Project, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} does not exist")
            return project
        project = await self._session.scalar(
            select(Project).where(Project.name == self._default_project_name).limit(1)
        )
        if project is None:
            project = Project(name=self._default_project_name, default_language="zh-CN")
            self._session.add(project)
            await self._session.flush()
        return project

    async def ingest(
        self, record: LiteratureRecord, *, project_id: UUID | None = None
    ) -> IngestionResult:
        publication_retracted = any(
            "retracted publication" in value.casefold()
            for value in record.publication_types
        )
        if publication_retracted and not record.is_retracted:
            record = record.model_copy(
                update={
                    "is_retracted": True,
                    "retraction_status": "retracted",
                    "quality_signals": {
                        **record.quality_signals,
                        "retraction_source": "publication_type",
                    },
                }
            )
        project = await self.ensure_project(project_id)
        paper = await self._find_paper(record)
        created = paper is None
        if paper is None:
            paper = Paper(
                pmid=record.pmid,
                pmcid=record.pmcid,
                doi=record.doi,
                doi_normalized=normalize_doi(record.doi),
                title=record.title,
                abstract=record.abstract,
                journal=record.journal,
                publication_year=record.publication_year,
                authors=[author.model_dump(mode="json") for author in record.authors],
                publication_types=record.publication_types,
                is_open_access=record.is_open_access,
                open_access_status=record.open_access_status,
                open_access_url=record.full_text_url,
                is_retracted=record.is_retracted,
                retraction_status=record.retraction_status,
                citation_count=record.citation_count,
                influential_citation_count=record.influential_citation_count,
                quality_signals=record.quality_signals,
                external_metadata=record.external_metadata,
                metadata_sources=list(dict.fromkeys([record.provider, *record.metadata_sources])),
            )
            self._session.add(paper)
            await self._session.flush()
        else:
            self._merge_paper(paper, record)

        await self._ensure_identifier(paper.id, record.provider, record.source_id)
        if record.pmid:
            await self._ensure_identifier(paper.id, "pmid", record.pmid)
        if record.pmcid:
            await self._ensure_identifier(paper.id, "pmcid", record.pmcid)
        if doi := normalize_doi(record.doi):
            await self._ensure_identifier(paper.id, "doi", doi)

        project_paper = await self._session.get(ProjectPaper, (project.id, paper.id))
        if project_paper is None:
            project_paper = ProjectPaper(project_id=project.id, paper_id=paper.id)
            self._session.add(project_paper)
            await self._session.flush()

        await self._ingest_mesh(paper.id, record)
        await self._session.flush()
        chunks_created = await self._ingest_abstract(paper, record)
        await self._session.flush()
        await TagService(self._session).auto_tag_paper(project.id, paper.id)
        project.retrieval_revision += 1
        await self._session.flush()
        return IngestionResult(
            project_id=project.id,
            paper_id=paper.id,
            created=created,
            chunks_created=chunks_created,
        )

    async def _find_paper(self, record: LiteratureRecord) -> Paper | None:
        conditions = []
        if record.pmid:
            conditions.append(Paper.pmid == record.pmid)
        if record.pmcid:
            conditions.append(Paper.pmcid == record.pmcid)
        if doi := normalize_doi(record.doi):
            conditions.append(Paper.doi_normalized == doi)
        if conditions:
            paper = await self._session.scalar(select(Paper).where(or_(*conditions)).limit(1))
            if paper:
                return paper
        identifier = await self._session.scalar(
            select(PaperIdentifier).where(
                PaperIdentifier.scheme == record.provider,
                PaperIdentifier.normalized_value == normalize_identifier(record.source_id),
            )
        )
        return await self._session.get(Paper, identifier.paper_id) if identifier else None

    @staticmethod
    def _merge_paper(paper: Paper, record: LiteratureRecord) -> None:
        paper.pmid = paper.pmid or record.pmid
        paper.pmcid = paper.pmcid or record.pmcid
        paper.doi = paper.doi or record.doi
        paper.doi_normalized = paper.doi_normalized or normalize_doi(record.doi)
        paper.journal = paper.journal or record.journal
        paper.publication_year = paper.publication_year or record.publication_year
        if record.abstract and (not paper.abstract or len(record.abstract) > len(paper.abstract)):
            paper.abstract = record.abstract
        if not paper.authors and record.authors:
            paper.authors = [author.model_dump(mode="json") for author in record.authors]
        paper.publication_types = list(
            dict.fromkeys([*paper.publication_types, *record.publication_types])
        )
        paper.is_open_access = paper.is_open_access or record.is_open_access
        if record.open_access_status != "unknown":
            paper.open_access_status = record.open_access_status
        paper.open_access_url = paper.open_access_url or record.full_text_url
        paper.is_retracted = paper.is_retracted or record.is_retracted
        if record.retraction_status != "unknown":
            paper.retraction_status = record.retraction_status
        if record.citation_count is not None:
            paper.citation_count = max(paper.citation_count or 0, record.citation_count)
        if record.influential_citation_count is not None:
            paper.influential_citation_count = max(
                paper.influential_citation_count or 0,
                record.influential_citation_count,
            )
        paper.quality_signals = {**paper.quality_signals, **record.quality_signals}
        paper.external_metadata = {**paper.external_metadata, **record.external_metadata}
        paper.metadata_sources = list(
            dict.fromkeys([*paper.metadata_sources, record.provider, *record.metadata_sources])
        )

    async def _ensure_identifier(self, paper_id: UUID, scheme: str, value: str) -> None:
        normalized = normalize_identifier(value)
        identifier = await self._session.scalar(
            select(PaperIdentifier).where(
                PaperIdentifier.scheme == scheme,
                PaperIdentifier.normalized_value == normalized,
            )
        )
        if identifier is None:
            self._session.add(
                PaperIdentifier(
                    paper_id=paper_id,
                    scheme=scheme,
                    value=value,
                    normalized_value=normalized,
                )
            )
        elif identifier.paper_id != paper_id:
            raise ValueError(f"Identifier {scheme}:{value} is already assigned to another paper")

    async def _ingest_mesh(self, paper_id: UUID, record: LiteratureRecord) -> None:
        for heading in record.mesh_headings:
            term = await self._session.get(MeSHTerm, heading.descriptor_ui)
            if term is None:
                self._session.add(
                    MeSHTerm(
                        descriptor_ui=heading.descriptor_ui,
                        preferred_label=heading.label,
                        tree_numbers=[],
                    )
                )
                await self._session.flush()
            mapping = await self._session.get(PaperMeSH, (paper_id, heading.descriptor_ui))
            if mapping is None:
                self._session.add(
                    PaperMeSH(
                        paper_id=paper_id,
                        descriptor_ui=heading.descriptor_ui,
                        is_major_topic=heading.is_major_topic,
                    )
                )
            elif heading.is_major_topic:
                mapping.is_major_topic = True

    async def _ingest_abstract(self, paper: Paper, record: LiteratureRecord) -> int:
        if not record.abstract:
            return 0
        content_hash = hashlib.sha256(record.abstract.encode("utf-8")).hexdigest()
        existing = await self._session.scalar(
            select(DocumentAsset).where(
                DocumentAsset.paper_id == paper.id,
                DocumentAsset.content_hash == content_hash,
            )
        )
        if existing:
            return 0
        source = {
            "pubmed": AssetSource.PUBMED,
            "europe_pmc": AssetSource.EUROPE_PMC,
            "openalex": AssetSource.OPENALEX,
            "crossref": AssetSource.CROSSREF,
        }.get(record.provider)
        if source is None:
            raise ValueError(f"Unsupported abstract source: {record.provider}")
        asset = DocumentAsset(
            paper_id=paper.id,
            source=source,
            evidence_depth=EvidenceDepth.ABSTRACT,
            parse_status=ParseStatus.READY,
            content_hash=content_hash,
            media_type="text/plain",
            access_url=record.full_text_url,
            parser_version="abstract-v1",
            cloud_processing_allowed=False,
        )
        self._session.add(asset)
        await self._session.flush()
        section = Section(
            asset_id=asset.id,
            section_path="Abstract",
            title="Abstract",
            ordinal=0,
        )
        self._session.add(section)
        await self._session.flush()
        drafts = await self._chunker.chunk(
            [
                SourceSegment(
                    text=record.abstract,
                    section_path="Abstract",
                    page_start=None,
                    page_end=None,
                    char_start=0,
                    char_end=len(record.abstract),
                )
            ]
        )
        chunks: list[Chunk] = []
        for draft in drafts:
            chunk = Chunk(
                section_id=section.id,
                ordinal=draft.ordinal,
                text=draft.text,
                content_hash=hashlib.sha256(draft.text.encode("utf-8")).hexdigest(),
                page_start=draft.source.page_start,
                page_end=draft.source.page_end,
                char_start=draft.source.char_start,
                char_end=draft.source.char_end,
                source_locator={
                    "section_path": draft.section_path,
                    "char_start": draft.source.char_start,
                    "char_end": draft.source.char_end,
                    "pmid": record.pmid,
                    "pmcid": record.pmcid,
                    "doi": normalize_doi(record.doi),
                },
            )
            self._session.add(chunk)
            chunks.append(chunk)
        await self._session.flush()
        for index, chunk in enumerate(chunks):
            chunk.previous_chunk_id = chunks[index - 1].id if index > 0 else None
            chunk.next_chunk_id = chunks[index + 1].id if index + 1 < len(chunks) else None
        return len(chunks)
