import hashlib
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.domain.contracts import ChunkingService
from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import Chunk, DocumentAsset, Paper, ProjectPaper, Section
from science_buddy.services.chunking import StructureAwareChunkingService
from science_buddy.services.documents import ParsedSection
from science_buddy.services.embeddings import get_embedding_service


class DocumentIngestionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        chunker: ChunkingService | None = None,
    ) -> None:
        self._session = session
        self._chunker = chunker or StructureAwareChunkingService(
            semantic_embedder=get_embedding_service()
        )

    async def ingest_sections(
        self,
        *,
        paper: Paper,
        source: AssetSource,
        evidence_depth: EvidenceDepth,
        content: bytes,
        sections: Sequence[ParsedSection],
        media_type: str,
        storage_path: Path | None = None,
        access_url: str | None = None,
        license_name: str | None = None,
        parser_version: str,
        extraction_metadata: dict[str, object] | None = None,
        cloud_processing_allowed: bool = False,
    ) -> tuple[DocumentAsset, int]:
        content_hash = hashlib.sha256(content).hexdigest()
        existing = await self._session.scalar(
            select(DocumentAsset).where(
                DocumentAsset.paper_id == paper.id,
                DocumentAsset.content_hash == content_hash,
            )
        )
        if existing:
            return existing, 0

        asset = DocumentAsset(
            paper_id=paper.id,
            source=source,
            evidence_depth=evidence_depth,
            parse_status=ParseStatus.PROCESSING,
            content_hash=content_hash,
            storage_path=str(storage_path) if storage_path else None,
            media_type=media_type,
            access_url=access_url,
            license_name=license_name,
            parser_version=parser_version,
            extraction_metadata=extraction_metadata or {},
            cloud_processing_allowed=cloud_processing_allowed,
        )
        self._session.add(asset)
        await self._session.flush()
        chunks_created = 0
        try:
            for parsed_section in sections:
                section = Section(
                    asset_id=asset.id,
                    section_path=parsed_section.section_path,
                    title=parsed_section.title,
                    ordinal=parsed_section.ordinal,
                )
                self._session.add(section)
                await self._session.flush()
                drafts = await self._chunker.chunk(parsed_section.segments)
                chunks: list[Chunk] = []
                seen_hashes: set[str] = set()
                for draft in drafts:
                    content_hash = hashlib.sha256(draft.text.encode("utf-8")).hexdigest()
                    if content_hash in seen_hashes:
                        continue
                    seen_hashes.add(content_hash)
                    chunk = Chunk(
                        section_id=section.id,
                        ordinal=len(chunks),
                        text=draft.text,
                        content_hash=content_hash,
                        page_start=draft.source.page_start,
                        page_end=draft.source.page_end,
                        char_start=draft.source.char_start,
                        char_end=draft.source.char_end,
                        source_locator={
                            "paper_id": str(paper.id),
                            "pmid": paper.pmid,
                            "pmcid": paper.pmcid,
                            "doi": paper.doi_normalized,
                            "section_path": draft.section_path,
                            "page_start": draft.source.page_start,
                            "page_end": draft.source.page_end,
                            "char_start": draft.source.char_start,
                            "char_end": draft.source.char_end,
                            "parser_version": parser_version,
                            "content_origin": (
                                (extraction_metadata or {}).get(
                                    "content_origin",
                                    "deterministic_extraction",
                                )
                            ),
                            "raw_asset_id": str(asset.id),
                        },
                    )
                    self._session.add(chunk)
                    chunks.append(chunk)
                await self._session.flush()
                for index, chunk in enumerate(chunks):
                    chunk.previous_chunk_id = chunks[index - 1].id if index > 0 else None
                    chunk.next_chunk_id = (
                        chunks[index + 1].id if index + 1 < len(chunks) else None
                    )
                chunks_created += len(chunks)
            asset.parse_status = ParseStatus.READY
            return asset, chunks_created
        except Exception:
            asset.parse_status = ParseStatus.FAILED
            raise


async def ensure_paper_in_project(
    session: AsyncSession,
    *,
    project_id: UUID,
    paper_id: UUID,
) -> Paper:
    link = await session.get(ProjectPaper, (project_id, paper_id))
    if link is None:
        raise ValueError("Paper is not part of the selected project")
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise ValueError("Paper does not exist")
    return paper
