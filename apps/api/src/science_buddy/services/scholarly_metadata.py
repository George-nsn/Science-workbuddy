import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.config import Settings
from science_buddy.infrastructure.models import (
    JournalMetric,
    Paper,
    PaperCitation,
    ProjectPaper,
)
from science_buddy.services.journal_metrics import OpenAlexJournalMetricsService
from science_buddy.services.literature.common import normalize_doi


@dataclass(frozen=True, slots=True)
class MetadataEnrichmentResult:
    paper_id: UUID
    sources: tuple[str, ...]
    references_linked: int
    errors: tuple[str, ...]


class ScholarlyMetadataService:
    """Fuse DOI/PMID metadata without treating popularity as scientific truth."""

    def __init__(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self._session = session
        self._client = client
        self._settings = settings
        self._journal_metrics = OpenAlexJournalMetricsService(client, settings)

    async def enrich_paper(self, paper: Paper) -> MetadataEnrichmentResult:
        doi = normalize_doi(paper.doi_normalized or paper.doi)
        calls: list[tuple[str, Any]] = []
        if doi:
            calls.extend(
                [
                    ("crossref", self._crossref(doi)),
                    ("openalex", self._openalex(doi)),
                ]
            )
            if self._settings.semantic_scholar_api_key:
                calls.append(
                    ("semantic_scholar", self._semantic_scholar(f"DOI:{doi}"))
                )
            if self._settings.unpaywall_email:
                calls.append(("unpaywall", self._unpaywall(doi)))
        elif paper.pmid and self._settings.semantic_scholar_api_key:
            calls.append(
                ("semantic_scholar", self._semantic_scholar(f"PMID:{paper.pmid}"))
            )
        if not calls:
            return MetadataEnrichmentResult(paper.id, (), 0, ("no_doi_or_pmid",))

        values = await asyncio.gather(
            *(operation for _, operation in calls),
            return_exceptions=True,
        )
        merged: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        referenced_dois: set[str] = set()
        for (source, _), value in zip(calls, values, strict=True):
            if isinstance(value, Exception):
                errors.append(f"{source}:{type(value).__name__}")
                continue
            if not isinstance(value, dict):
                errors.append(f"{source}:invalid_payload")
                continue
            merged[source] = value
            referenced_dois.update(str(item) for item in value.pop("_referenced_dois", []))

        await self._apply(paper, merged)
        references_linked = await self._link_local_references(paper.id, referenced_dois)
        await self._session.flush()
        return MetadataEnrichmentResult(
            paper.id,
            tuple(merged),
            references_linked,
            tuple(errors),
        )

    async def enrich_project(
        self,
        project_id: UUID,
        paper_ids: list[UUID] | None = None,
    ) -> list[MetadataEnrichmentResult]:
        statement = (
            select(Paper)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(ProjectPaper.project_id == project_id)
            .order_by(Paper.created_at)
        )
        if paper_ids:
            statement = statement.where(Paper.id.in_(paper_ids))
        papers = list((await self._session.scalars(statement.limit(100))).all())
        results = [await self.enrich_paper(paper) for paper in papers]
        await self._session.commit()
        return results

    async def _crossref(self, doi: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{self._settings.crossref_base_url.rstrip('/')}/works/{quote(doi, safe='')}",
        )
        response.raise_for_status()
        message = response.json().get("message", {})
        references = {
            normalized
            for item in message.get("reference", [])
            if isinstance(item, dict)
            and (normalized := normalize_doi(item.get("DOI")))
        }
        relation = message.get("relation", {})
        retracted = bool(relation.get("is-retracted-by")) or "retraction" in str(
            message.get("subtype", "")
        ).casefold()
        return {
            "publisher": message.get("publisher"),
            "type": message.get("type"),
            "container_titles": message.get("container-title") or [],
            "issn": message.get("ISSN") or [],
            "member": message.get("member"),
            "reference_count": message.get("reference-count"),
            "is_retracted": retracted,
            "relation": relation,
            "_referenced_dois": sorted(references),
        }

    async def _openalex(self, doi: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{self._settings.openalex_base_url.rstrip('/')}/works",
            params={"filter": f"doi:{doi}", "per-page": "1"},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        value = results[0] if isinstance(results, list) and results else {}
        returned_doi = normalize_doi(value.get("doi"))
        if returned_doi != doi:
            raise ValueError("OpenAlex DOI lookup returned a non-matching work")
        oa = value.get("open_access") or {}
        best = value.get("best_oa_location") or {}
        primary_source = (value.get("primary_location") or {}).get("source")
        source_metrics: dict[str, Any] = {}
        if isinstance(primary_source, dict) and primary_source.get("id"):
            source_metrics = await self._journal_metrics.fetch(str(primary_source["id"]))
        return {
            "id": value.get("id"),
            "doi": returned_doi,
            "cited_by_count": value.get("cited_by_count"),
            "is_retracted": bool(value.get("is_retracted")),
            "is_open_access": bool(oa.get("is_oa")),
            "open_access_status": oa.get("oa_status"),
            "open_access_url": best.get("pdf_url") or best.get("landing_page_url"),
            "primary_source": primary_source,
            "source_metrics": source_metrics,
            "_referenced_dois": [],
        }

    async def _semantic_scholar(self, identifier: str) -> dict[str, Any]:
        headers = {}
        if self._settings.semantic_scholar_api_key:
            headers["x-api-key"] = self._settings.semantic_scholar_api_key.get_secret_value()
        fields = (
            "externalIds,citationCount,influentialCitationCount,isOpenAccess,"
            "openAccessPdf,venue,publicationVenue,references.externalIds"
        )
        response = await self._client.get(
            f"{self._settings.semantic_scholar_base_url.rstrip('/')}/paper/"
            f"{quote(identifier, safe=':')}",
            params={"fields": fields},
            headers=headers,
        )
        response.raise_for_status()
        value = response.json()
        referenced = {
            normalized
            for item in value.get("references", [])
            if isinstance(item, dict)
            and isinstance(item.get("externalIds"), dict)
            and (normalized := normalize_doi(item["externalIds"].get("DOI")))
        }
        return {
            "paper_id": value.get("paperId"),
            "citation_count": value.get("citationCount"),
            "influential_citation_count": value.get("influentialCitationCount"),
            "is_open_access": bool(value.get("isOpenAccess")),
            "open_access_url": (value.get("openAccessPdf") or {}).get("url"),
            "venue": value.get("venue"),
            "publication_venue": value.get("publicationVenue"),
            "external_ids": value.get("externalIds"),
            "_referenced_dois": sorted(referenced),
        }

    async def _unpaywall(self, doi: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{self._settings.unpaywall_base_url.rstrip('/')}/{quote(doi, safe='')}",
            params={"email": self._settings.unpaywall_email},
        )
        response.raise_for_status()
        value = response.json()
        best = value.get("best_oa_location") or {}
        return {
            "is_open_access": bool(value.get("is_oa")),
            "open_access_status": value.get("oa_status"),
            "open_access_url": best.get("url_for_pdf") or best.get("url"),
            "genre": value.get("genre"),
            "_referenced_dois": [],
        }

    async def _apply(self, paper: Paper, values: dict[str, dict[str, Any]]) -> None:
        openalex = values.get("openalex", {})
        semantic = values.get("semantic_scholar", {})
        unpaywall = values.get("unpaywall", {})
        crossref = values.get("crossref", {})
        paper.is_open_access = any(
            bool(source.get("is_open_access"))
            for source in (openalex, semantic, unpaywall)
        ) or paper.is_open_access
        oa_sources = (unpaywall, openalex, semantic)
        paper.open_access_status = next(
            (
                str(status)
                for source in oa_sources
                if source.get("is_open_access")
                and (status := source.get("open_access_status"))
            ),
            paper.open_access_status,
        )
        paper.open_access_url = next(
            (
                str(url)
                for url in (
                    unpaywall.get("open_access_url"),
                    openalex.get("open_access_url"),
                    semantic.get("open_access_url"),
                )
                if url
            ),
            paper.open_access_url,
        )
        paper.is_retracted = paper.is_retracted or bool(
            openalex.get("is_retracted") or crossref.get("is_retracted")
        )
        if paper.is_retracted:
            paper.retraction_status = "retracted"
        citation_counts = [
            int(value)
            for value in (
                openalex.get("cited_by_count"),
                semantic.get("citation_count"),
            )
            if isinstance(value, int)
        ]
        if citation_counts:
            paper.citation_count = max([paper.citation_count or 0, *citation_counts])
        influential = semantic.get("influential_citation_count")
        if isinstance(influential, int):
            paper.influential_citation_count = max(
                paper.influential_citation_count or 0,
                influential,
            )
        primary_source = openalex.get("primary_source")
        if not isinstance(primary_source, dict):
            primary_source = {}
        publication_venue = semantic.get("publication_venue")
        if not isinstance(publication_venue, dict):
            publication_venue = {}
        source_metrics = openalex.get("source_metrics")
        if not isinstance(source_metrics, dict):
            source_metrics = {}
        if source_metrics.get("source_id") and source_metrics.get("display_name"):
            openalex_source_id = str(source_metrics["source_id"]).rsplit("/", 1)[-1]
            journal_metric = await self._session.scalar(
                select(JournalMetric).where(
                    JournalMetric.openalex_source_id == openalex_source_id
                )
            )
            if journal_metric is None:
                journal_metric = JournalMetric(
                    openalex_source_id=openalex_source_id,
                    display_name=str(source_metrics["display_name"]),
                    metric_source="openalex",
                    metric_note=str(source_metrics.get("metric_note") or ""),
                )
                self._session.add(journal_metric)
            journal_metric.issn_l = (
                str(source_metrics["issn_l"])
                if source_metrics.get("issn_l")
                else None
            )
            journal_metric.issn = [str(value) for value in source_metrics.get("issn", [])]
            journal_metric.display_name = str(source_metrics["display_name"])
            journal_metric.two_year_mean_citedness = source_metrics.get(
                "two_year_mean_citedness"
            )
            journal_metric.h_index = source_metrics.get("h_index")
            journal_metric.i10_index = source_metrics.get("i10_index")
            journal_metric.open_quartile = source_metrics.get("quartile")
            journal_metric.percentile = source_metrics.get("percentile")
            journal_metric.comparison_count = source_metrics.get("comparison_count")
            journal_metric.quartile_basis = source_metrics.get("quartile_basis") or {}
            journal_metric.importance_score = float(
                source_metrics.get("importance_score") or 0.0
            )
            journal_metric.metric_updated_date = source_metrics.get("updated_date")
            journal_metric.metric_note = str(source_metrics.get("metric_note") or "")
            await self._session.flush()
            paper.journal_metric_id = journal_metric.id
        journal_signals = {
            "display_name": (
                primary_source.get("display_name")
                or publication_venue.get("name")
                or semantic.get("venue")
                or paper.journal
            ),
            "type": primary_source.get("type") or publication_venue.get("type"),
            "issn_l": primary_source.get("issn_l"),
            "issn": primary_source.get("issn") or crossref.get("issn") or [],
            "is_in_doaj": primary_source.get("is_in_doaj"),
            "is_core": primary_source.get("is_core"),
            "is_open_access": primary_source.get("is_oa"),
            "publisher": crossref.get("publisher"),
            "openalex_source_id": primary_source.get("id"),
            "semantic_scholar_venue_id": publication_venue.get("id"),
            "two_year_mean_citedness": source_metrics.get(
                "two_year_mean_citedness"
            ),
            "h_index": source_metrics.get("h_index"),
            "open_quartile": source_metrics.get("quartile"),
            "open_percentile": source_metrics.get("percentile"),
            "importance_score": source_metrics.get("importance_score"),
            "metric_source": source_metrics.get("source"),
            "metric_updated_date": source_metrics.get("updated_date"),
            "quartile_basis": source_metrics.get("quartile_basis"),
            "metric_note": source_metrics.get("metric_note"),
        }
        journal_signals = {
            key: value for key, value in journal_signals.items() if value is not None
        }
        retraction_sources = [
            source
            for source, payload in (("crossref", crossref), ("openalex", openalex))
            if payload.get("is_retracted")
        ]
        open_access_sources = [
            source
            for source, payload in (
                ("openalex", openalex),
                ("semantic_scholar", semantic),
                ("unpaywall", unpaywall),
            )
            if payload.get("is_open_access")
        ]
        paper.metadata_sources = list(dict.fromkeys([*paper.metadata_sources, *values]))
        paper.external_metadata = {**paper.external_metadata, **values}
        paper.quality_signals = {
            **paper.quality_signals,
            "metadata_source_count": len(paper.metadata_sources),
            "has_doi": bool(paper.doi_normalized or paper.doi),
            "has_pmid": bool(paper.pmid),
            "is_open_access": paper.is_open_access,
            "retraction_status": paper.retraction_status,
            "citation_count": paper.citation_count,
            "influential_citation_count": paper.influential_citation_count,
            "journal": journal_signals,
            "retraction_sources": retraction_sources,
            "open_access_sources": open_access_sources,
            "quality_note": (
                "OpenAlex venue impact signals are secondary ranking/filtering aids; "
                "they are not Clarivate JIF/JCR, CAS, or SJR data and do not establish "
                "scientific validity."
            ),
        }

    async def _link_local_references(
        self,
        source_paper_id: UUID,
        referenced_dois: set[str],
    ) -> int:
        if not referenced_dois:
            return 0
        targets = list(
            (
                await self._session.scalars(
                    select(Paper).where(Paper.doi_normalized.in_(referenced_dois))
                )
            ).all()
        )
        target_ids = {target.id for target in targets if target.id != source_paper_id}
        existing_target_ids = set(
            (
                await self._session.scalars(
                    select(PaperCitation.target_paper_id).where(
                        PaperCitation.source_paper_id == source_paper_id,
                        PaperCitation.target_paper_id.in_(target_ids),
                    )
                )
            ).all()
        )
        linked = 0
        for target in targets:
            if target.id == source_paper_id or target.id in existing_target_ids:
                continue
            await self._session.execute(
                sqlite_insert(PaperCitation)
                .values(
                    source_paper_id=source_paper_id,
                    target_paper_id=target.id,
                    source_name="metadata_fusion",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        PaperCitation.source_paper_id,
                        PaperCitation.target_paper_id,
                    ]
                )
            )
            existing_target_ids.add(target.id)
            linked += 1
        return linked