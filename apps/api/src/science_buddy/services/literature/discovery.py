import asyncio
import re
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, Field

from science_buddy.domain.providers import (
    AuthorRecord,
    LiteratureProvider,
    LiteratureRecord,
    MeSHHeadingRecord,
)
from science_buddy.services.literature.common import normalize_doi


class SourceReference(BaseModel):
    provider: str
    source_id: str


class LiteratureCandidate(BaseModel):
    key: str
    title: str
    abstract: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    journal: str | None = None
    publication_date: date | None = None
    publication_year: int | None = None
    authors: list[AuthorRecord] = Field(default_factory=list)
    publication_types: list[str] = Field(default_factory=list)
    mesh_headings: list[MeSHHeadingRecord] = Field(default_factory=list)
    is_open_access: bool = False
    full_text_url: str | None = None
    sources: list[SourceReference] = Field(default_factory=list)


def _record_key(record: LiteratureRecord) -> str:
    if record.pmid:
        return f"pmid:{record.pmid.strip()}"
    if doi := normalize_doi(record.doi):
        return f"doi:{doi}"
    if record.pmcid:
        return f"pmcid:{record.pmcid.strip().lower()}"
    normalized_title = re.sub(r"\W+", " ", record.title.casefold()).strip()
    return f"title:{normalized_title}:{record.publication_year or 0}"


def _from_record(record: LiteratureRecord) -> LiteratureCandidate:
    return LiteratureCandidate(
        key=_record_key(record),
        title=record.title,
        abstract=record.abstract,
        pmid=record.pmid,
        pmcid=record.pmcid,
        doi=normalize_doi(record.doi),
        journal=record.journal,
        publication_date=record.publication_date,
        publication_year=record.publication_year,
        authors=record.authors,
        publication_types=record.publication_types,
        mesh_headings=record.mesh_headings,
        is_open_access=record.is_open_access,
        full_text_url=record.full_text_url,
        sources=[SourceReference(provider=record.provider, source_id=record.source_id)],
    )


def _merge(current: LiteratureCandidate, incoming: LiteratureRecord) -> LiteratureCandidate:
    incoming_source = SourceReference(provider=incoming.provider, source_id=incoming.source_id)
    sources = list(current.sources)
    if incoming_source not in sources:
        sources.append(incoming_source)
    authors = current.authors or incoming.authors
    publication_types = list(
        dict.fromkeys([*current.publication_types, *incoming.publication_types])
    )
    mesh_by_ui = {heading.descriptor_ui: heading for heading in current.mesh_headings}
    for heading in incoming.mesh_headings:
        existing = mesh_by_ui.get(heading.descriptor_ui)
        if existing is None or heading.is_major_topic:
            mesh_by_ui[heading.descriptor_ui] = heading
    abstract = current.abstract
    if incoming.abstract and (not abstract or len(incoming.abstract) > len(abstract)):
        abstract = incoming.abstract
    return current.model_copy(
        update={
            "abstract": abstract,
            "pmid": current.pmid or incoming.pmid,
            "pmcid": current.pmcid or incoming.pmcid,
            "doi": current.doi or normalize_doi(incoming.doi),
            "journal": current.journal or incoming.journal,
            "publication_date": current.publication_date or incoming.publication_date,
            "publication_year": current.publication_year or incoming.publication_year,
            "authors": authors,
            "publication_types": publication_types,
            "mesh_headings": list(mesh_by_ui.values()),
            "is_open_access": current.is_open_access or incoming.is_open_access,
            "full_text_url": current.full_text_url or incoming.full_text_url,
            "sources": sources,
        }
    )


def group_literature_records(
    records: Sequence[LiteratureRecord],
) -> list[tuple[LiteratureCandidate, list[LiteratureRecord]]]:
    merged: dict[str, LiteratureCandidate] = {}
    grouped: dict[str, list[LiteratureRecord]] = {}
    order: list[str] = []
    for record in records:
        key = _record_key(record)
        if key not in merged:
            merged[key] = _from_record(record)
            grouped[key] = [record]
            order.append(key)
        else:
            merged[key] = _merge(merged[key], record)
            grouped[key].append(record)
    return [(merged[key], grouped[key]) for key in order]


def interleave_literature_batches(
    batches: Sequence[Sequence[LiteratureRecord]],
) -> list[LiteratureRecord]:
    longest = max((len(batch) for batch in batches), default=0)
    return [
        batch[index]
        for index in range(longest)
        for batch in batches
        if index < len(batch)
    ]


class LiteratureDiscoveryService:
    def __init__(self, providers: Sequence[LiteratureProvider]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    @property
    def provider_names(self) -> set[str]:
        return set(self._providers)

    async def search(
        self,
        query: str,
        *,
        provider_names: Sequence[str],
        limit: int,
        tolerate_failures: bool = False,
    ) -> list[LiteratureCandidate]:
        selected = [self._providers[name] for name in provider_names]
        per_provider_limit = max(limit, 1)
        batches = await asyncio.gather(
            *(provider.search(query, limit=per_provider_limit) for provider in selected),
            return_exceptions=tolerate_failures,
        )
        successful_batches = [
            batch for batch in batches if not isinstance(batch, BaseException)
        ]
        records = interleave_literature_batches(successful_batches)
        return [candidate for candidate, _ in group_literature_records(records)[:limit]]

    async def fetch_references(
        self, references: Sequence[SourceReference]
    ) -> list[LiteratureRecord]:
        grouped: dict[str, list[str]] = {}
        for reference in references:
            grouped.setdefault(reference.provider, []).append(reference.source_id)
        records: list[LiteratureRecord] = []
        for provider_name, source_ids in grouped.items():
            records.extend(await self._providers[provider_name].fetch_many(source_ids))
        return records
