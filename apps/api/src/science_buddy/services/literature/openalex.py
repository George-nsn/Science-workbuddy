from collections.abc import Sequence
from datetime import date
from typing import Any, cast
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


def _abstract(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    positioned = [
        (int(position), str(term))
        for term, positions in value.items()
        if isinstance(positions, list)
        for position in positions
        if isinstance(position, int)
    ]
    return " ".join(term for _, term in sorted(positioned)) or None


def parse_openalex_work(value: dict[str, object]) -> LiteratureRecord | None:
    source_id = str(value.get("id") or "").rsplit("/", 1)[-1]
    title = str(value.get("display_name") or value.get("title") or "").strip()
    if not source_id or not title:
        return None
    ids = (
        cast(dict[str, Any], value.get("ids"))
        if isinstance(value.get("ids"), dict)
        else {}
    )
    primary_location = cast(
        dict[str, Any],
        value.get("primary_location")
        if isinstance(value.get("primary_location"), dict)
        else {},
    )
    source = cast(
        dict[str, Any],
        primary_location.get("source")
        if isinstance(primary_location.get("source"), dict)
        else {},
    )
    open_access = cast(
        dict[str, Any],
        value.get("open_access") if isinstance(value.get("open_access"), dict) else {},
    )
    best_location = cast(
        dict[str, Any],
        value.get("best_oa_location")
        if isinstance(value.get("best_oa_location"), dict)
        else {},
    )
    authors: list[AuthorRecord] = []
    raw_authorships = value.get("authorships")
    for raw in raw_authorships if isinstance(raw_authorships, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("author"), dict):
            continue
        author = cast(dict[str, Any], raw["author"])
        name = str(author.get("display_name") or "").strip()
        if name:
            authors.append(AuthorRecord(full_name=name))
    publication_date = None
    raw_date = str(value.get("publication_date") or "")
    try:
        publication_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        publication_date = None
    doi = normalize_doi(
        str(value.get("doi") or ids.get("doi") or "") or None
    )
    pmid = str(ids.get("pmid") or "").rsplit("/", 1)[-1] or None
    pmcid = str(ids.get("pmcid") or "").rsplit("/", 1)[-1] or None
    return LiteratureRecord(
        provider="openalex",
        source_id=source_id,
        title=title,
        abstract=_abstract(value.get("abstract_inverted_index")),
        pmid=pmid,
        pmcid=pmcid,
        doi=doi,
        journal=str(source.get("display_name") or "").strip() or None,
        publication_date=publication_date,
        publication_year=parse_year(value.get("publication_year")),
        authors=authors,
        publication_types=[str(value.get("type"))] if value.get("type") else [],
        is_open_access=bool(open_access.get("is_oa")),
        full_text_url=(
            str(best_location.get("pdf_url") or best_location.get("landing_page_url") or "")
            or None
        ),
        open_access_status=str(open_access.get("oa_status") or "unknown"),
        is_retracted=bool(value.get("is_retracted")),
        retraction_status="retracted" if value.get("is_retracted") else "unknown",
        citation_count=(
            cast(int, value["cited_by_count"])
            if isinstance(value.get("cited_by_count"), int)
            else None
        ),
        quality_signals={"openalex_relevance_score": value.get("relevance_score")},
        external_metadata={
            "openalex_discovery": {
                "id": value.get("id"),
                "primary_source": source,
                "primary_topic": value.get("primary_topic"),
            }
        },
        metadata_sources=["openalex"],
    )


class OpenAlexProvider:
    name = "openalex"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._limiter = AsyncRateLimiter(5)

    def _params(self, values: dict[str, str]) -> dict[str, str]:
        return {**values, **({"api_key": self._api_key} if self._api_key else {})}

    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]:
        await self._limiter.wait()
        response = await self._client.get(
            f"{self._base_url}/works",
            params=self._params(
                {
                    "search": query,
                    "per-page": str(min(limit, 100)),
                    "sort": "relevance_score:desc",
                }
            ),
        )
        response.raise_for_status()
        raw = response.json().get("results", [])
        if not isinstance(raw, list):
            raise LiteratureProviderError("OpenAlex returned an unexpected search response")
        return [
            record
            for item in raw
            if isinstance(item, dict) and (record := parse_openalex_work(item))
        ]

    async def fetch(self, source_id: str) -> LiteratureRecord:
        normalized = source_id.strip().rsplit("/", 1)[-1]
        if not normalized.startswith("W"):
            raise LiteratureProviderError("OpenAlex work identifiers must start with W")
        await self._limiter.wait()
        response = await self._client.get(
            f"{self._base_url}/works/{quote(normalized, safe='')}",
            params=self._params({}),
        )
        if response.status_code == 404:
            raise LiteratureRecordNotFoundError(f"OpenAlex work {source_id!r} was not found")
        response.raise_for_status()
        record = parse_openalex_work(response.json())
        if record is None:
            raise LiteratureProviderError("OpenAlex returned an incomplete work")
        return record

    async def fetch_many(self, source_ids: Sequence[str]) -> list[LiteratureRecord]:
        return [await self.fetch(source_id) for source_id in source_ids]