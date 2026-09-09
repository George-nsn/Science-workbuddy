from collections.abc import Sequence
from datetime import date
from typing import cast
from urllib.parse import quote

import httpx

from science_buddy.domain.providers import AuthorRecord, LiteratureRecord
from science_buddy.services.literature.common import (
    LiteratureProviderError,
    LiteratureRecordNotFoundError,
    normalize_doi,
    parse_year,
)
from science_buddy.services.literature.rate_limit import AsyncRateLimiter


def _first(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0]).strip() or None
    return None


def _date_parts(value: object) -> date | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return None
    numbers = [int(item) for item in parts[0][:3] if isinstance(item, int)]
    if not numbers:
        return None
    try:
        return date(
            numbers[0],
            numbers[1] if len(numbers) > 1 else 1,
            numbers[2] if len(numbers) > 2 else 1,
        )
    except ValueError:
        return None


def parse_crossref_work(value: dict[str, object]) -> LiteratureRecord | None:
    doi = normalize_doi(str(value.get("DOI") or "") or None)
    title = _first(value.get("title"))
    if not doi or not title:
        return None
    authors: list[AuthorRecord] = []
    raw_authors = value.get("author")
    for raw in raw_authors if isinstance(raw_authors, list) else []:
        if not isinstance(raw, dict):
            continue
        given = str(raw.get("given") or "").strip() or None
        family = str(raw.get("family") or "").strip() or None
        full_name = " ".join(item for item in (given, family) if item)
        if full_name:
            authors.append(
                AuthorRecord(full_name=full_name, given_name=given, family_name=family)
            )
    published = _date_parts(
        value.get("published-print")
        or value.get("published-online")
        or value.get("issued")
    )
    abstract = str(value.get("abstract") or "").strip() or None
    return LiteratureRecord(
        provider="crossref",
        source_id=doi,
        title=title,
        abstract=abstract,
        doi=doi,
        journal=_first(value.get("container-title")),
        publication_date=published,
        publication_year=published.year if published else parse_year(value.get("published")),
        authors=authors,
        publication_types=[str(value.get("type"))] if value.get("type") else [],
        citation_count=(
            cast(int, value["is-referenced-by-count"])
            if isinstance(value.get("is-referenced-by-count"), int)
            else None
        ),
        quality_signals={"crossref_score": value.get("score")},
        external_metadata={
            "crossref_discovery": {
                "publisher": value.get("publisher"),
                "issn": value.get("ISSN") or [],
                "type": value.get("type"),
            }
        },
        metadata_sources=["crossref"],
    )


class CrossrefProvider:
    name = "crossref"

    def __init__(self, client: httpx.AsyncClient, *, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._limiter = AsyncRateLimiter(5)

    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]:
        await self._limiter.wait()
        response = await self._client.get(
            f"{self._base_url}/works",
            params={
                "query.bibliographic": query,
                "rows": str(min(limit, 100)),
                "select": (
                    "DOI,title,abstract,author,container-title,published-print,"
                    "published-online,issued,type,publisher,ISSN,"
                    "is-referenced-by-count,score"
                ),
            },
        )
        response.raise_for_status()
        raw = (response.json().get("message") or {}).get("items", [])
        if not isinstance(raw, list):
            raise LiteratureProviderError("Crossref returned an unexpected search response")
        return [
            record
            for item in raw
            if isinstance(item, dict) and (record := parse_crossref_work(item))
        ]

    async def fetch(self, source_id: str) -> LiteratureRecord:
        doi = normalize_doi(source_id)
        if not doi:
            raise LiteratureProviderError("Crossref requires a DOI")
        await self._limiter.wait()
        response = await self._client.get(f"{self._base_url}/works/{quote(doi, safe='')}")
        if response.status_code == 404:
            raise LiteratureRecordNotFoundError(f"Crossref work {source_id!r} was not found")
        response.raise_for_status()
        record = parse_crossref_work(response.json().get("message") or {})
        if record is None:
            raise LiteratureProviderError("Crossref returned an incomplete work")
        return record

    async def fetch_many(self, source_ids: Sequence[str]) -> list[LiteratureRecord]:
        return [await self.fetch(source_id) for source_id in source_ids]