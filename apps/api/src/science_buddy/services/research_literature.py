import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.config import Settings
from science_buddy.domain.providers import LiteratureProvider, LiteratureRecord
from science_buddy.infrastructure.models import BrainstormLiterature, Paper
from science_buddy.services.literature import (
    CrossrefProvider,
    EuropePmcProvider,
    OpenAlexProvider,
    PubMedProvider,
)
from science_buddy.services.literature.discovery import (
    group_literature_records,
    interleave_literature_batches,
)
from science_buddy.services.literature.ingestion import LiteratureIngestionService
from science_buddy.services.scholarly_metadata import ScholarlyMetadataService
from science_buddy.services.tags import TagService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LiteratureSupplementResult:
    paper_ids: tuple[UUID, ...]
    associated_count: int
    target: int
    providers: tuple[str, ...]
    failed_providers: tuple[str, ...]


class ResearchLiteratureSupplementer:
    """Grow a session's literature set toward a bounded target across open providers."""

    def __init__(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._client = client
        self._settings = settings

    async def supplement_brainstorm(
        self,
        *,
        project_id: UUID,
        session_id: UUID,
        session_number: int,
        queries: list[str],
        turn_number: int,
        target: int | None = None,
    ) -> LiteratureSupplementResult:
        desired = target or self._settings.research_literature_target
        associated = int(
            (
                await self._session.scalar(
                    select(func.count()).select_from(BrainstormLiterature).where(
                        BrainstormLiterature.session_id == session_id
                    )
                )
            )
            or 0
        )
        needed = max(0, desired - associated)
        if needed == 0:
            return LiteratureSupplementResult((), associated, desired, (), ())
        providers = self._providers()
        normalized_queries = list(
            dict.fromkeys(
                " ".join(query.split())[:500]
                for query in queries[: self._settings.research_literature_queries]
                if query.strip()
            )
        )
        if not normalized_queries:
            return LiteratureSupplementResult((), associated, desired, tuple(providers), ())
        per_query_limit = min(
            20,
            max(5, (needed + len(normalized_queries) - 1) // len(normalized_queries)),
        )
        calls = [
            (query, provider.name, provider.search(query, limit=per_query_limit))
            for query in normalized_queries
            for provider in providers.values()
        ]
        values = await asyncio.gather(
            *(operation for _, _, operation in calls),
            return_exceptions=True,
        )
        successful_batches: list[list[LiteratureRecord]] = []
        failed: set[str] = set()
        query_by_record: dict[tuple[str, str], str] = {}
        for (query, provider_name, _), value in zip(calls, values, strict=True):
            if isinstance(value, BaseException):
                failed.add(provider_name)
                logger.warning(
                    "research_literature_provider_failed provider=%s reason=%s",
                    provider_name,
                    type(value).__name__,
                )
                continue
            successful_batches.append(value)
            for record in value:
                query_by_record.setdefault((record.provider, record.source_id), query)
        records = interleave_literature_batches(successful_batches)
        if not records:
            return LiteratureSupplementResult(
                (), associated, desired, tuple(providers), tuple(sorted(failed))
            )
        # Per-turn bounded ingestion to prevent long I/O blocking (up to 5 papers per turn)
        turn_batch_limit = min(needed, 5)
        selected = group_literature_records(records)[:turn_batch_limit]
        ingestor = LiteratureIngestionService(
            self._session,
            default_project_name=self._settings.default_project_name,
        )
        paper_ids: list[UUID] = []
        papers_to_enrich: list[Paper] = []
        labels = ["实验参考", f"会话{session_number}", "自动检索"]
        for candidate, records_for_candidate in selected:
            if not records_for_candidate:
                continue
            result = await ingestor.ingest(records_for_candidate[0], project_id=project_id)
            for duplicate in records_for_candidate[1:]:
                await ingestor.ingest(duplicate, project_id=project_id)
            matched_query = next(
                (
                    query_by_record.get((source.provider, source.source_id))
                    for source in candidate.sources
                    if query_by_record.get((source.provider, source.source_id))
                ),
                None,
            )
            query = matched_query or normalized_queries[0]
            await self._session.execute(
                sqlite_insert(BrainstormLiterature)
                .values(
                    session_id=session_id,
                    paper_id=result.paper_id,
                    turn_number=turn_number,
                    query=query,
                    tags_applied=labels,
                )
                .on_conflict_do_update(
                    index_elements=[
                        BrainstormLiterature.session_id,
                        BrainstormLiterature.paper_id,
                    ],
                    set_={
                        "turn_number": turn_number,
                        "query": query,
                        "tags_applied": labels,
                    },
                )
            )
            await TagService(self._session).add_session_tags(
                project_id,
                result.paper_id,
                labels,
            )
            paper_ids.append(result.paper_id)
            paper = await self._session.get(Paper, result.paper_id)
            if paper is not None and (paper.doi_normalized or paper.doi):
                papers_to_enrich.append(paper)
        metadata_service = ScholarlyMetadataService(
            self._session,
            self._client,
            self._settings,
        )

        async def _safe_enrich(paper_item: Paper) -> None:
            try:
                await asyncio.wait_for(metadata_service.enrich_paper(paper_item), timeout=3.5)
            except (TimeoutError, httpx.HTTPError, ValueError, Exception) as exc:
                logger.warning(
                    "research_literature_metadata_failed paper_id=%s reason=%s",
                    paper_item.id,
                    type(exc).__name__,
                )

        if papers_to_enrich:
            await asyncio.gather(
                *(_safe_enrich(p) for p in papers_to_enrich[:5]),
                return_exceptions=True,
            )
        await self._session.commit()
        final_count = int(
            (
                await self._session.scalar(
                    select(func.count()).select_from(BrainstormLiterature).where(
                        BrainstormLiterature.session_id == session_id
                    )
                )
            )
            or 0
        )
        return LiteratureSupplementResult(
            tuple(dict.fromkeys(paper_ids)),
            final_count,
            desired,
            tuple(providers),
            tuple(sorted(failed)),
        )

    def _providers(self) -> dict[str, LiteratureProvider]:
        values: list[LiteratureProvider] = [
            PubMedProvider(
                self._client,
                base_url=self._settings.ncbi_base_url,
                tool=self._settings.ncbi_tool,
                email=self._settings.ncbi_email,
                api_key=(
                    self._settings.ncbi_api_key.get_secret_value()
                    if self._settings.ncbi_api_key
                    else None
                ),
            ),
            EuropePmcProvider(self._client, base_url=self._settings.europe_pmc_base_url),
            OpenAlexProvider(
                self._client,
                base_url=self._settings.openalex_base_url,
                api_key=(
                    self._settings.openalex_api_key.get_secret_value()
                    if self._settings.openalex_api_key
                    else None
                ),
            ),
            CrossrefProvider(self._client, base_url=self._settings.crossref_base_url),
        ]
        return {provider.name: provider for provider in values}