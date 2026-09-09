import asyncio
from collections.abc import Sequence

import httpx

from science_buddy.domain.providers import (
    AuthorRecord,
    LiteratureRecord,
    MeSHHeadingRecord,
)
from science_buddy.services.literature.common import (
    LiteratureProviderError,
    LiteratureRecordNotFoundError,
    normalize_doi,
    parse_year,
    safe_date,
)
from science_buddy.services.literature.rate_limit import AsyncRateLimiter


def _as_list(container: object, key: str) -> list[object]:
    if not isinstance(container, dict):
        return []
    value = container.get(key, [])
    return value if isinstance(value, list) else []


def parse_europe_pmc_result(item: dict[str, object]) -> LiteratureRecord | None:
    source = str(item.get("source") or "").strip()
    external_id = str(item.get("id") or item.get("pmid") or item.get("pmcid") or "").strip()
    title = str(item.get("title") or "").strip()
    if not source or not external_id or not title:
        return None

    author_list = item.get("authorList")
    authors: list[AuthorRecord] = []
    for raw_author in _as_list(author_list, "author"):
        if not isinstance(raw_author, dict):
            continue
        full_name = str(raw_author.get("fullName") or "").strip()
        if full_name:
            authors.append(
                AuthorRecord(
                    full_name=full_name,
                    family_name=str(raw_author.get("lastName") or "").strip() or None,
                    given_name=str(raw_author.get("firstName") or "").strip() or None,
                )
            )

    mesh_list = item.get("meshHeadingList")
    mesh_headings: list[MeSHHeadingRecord] = []
    for raw_heading in _as_list(mesh_list, "meshHeading"):
        if not isinstance(raw_heading, dict):
            continue
        descriptor_ui = str(
            raw_heading.get("majorTopic_YN") and raw_heading.get("descriptorName_UI")
            or raw_heading.get("descriptorName_UI")
            or raw_heading.get("descriptorUI")
            or ""
        ).strip()
        label = str(raw_heading.get("descriptorName") or "").strip()
        if descriptor_ui and label:
            mesh_headings.append(
                MeSHHeadingRecord(
                    descriptor_ui=descriptor_ui,
                    label=label,
                    is_major_topic=str(raw_heading.get("majorTopic_YN") or "N") == "Y",
                )
            )

    publication_year = parse_year(item.get("pubYear") or item.get("firstPublicationDate"))
    first_date = str(item.get("firstPublicationDate") or "")
    month = int(first_date[5:7]) if len(first_date) >= 7 and first_date[5:7].isdigit() else 1
    day = int(first_date[8:10]) if len(first_date) >= 10 and first_date[8:10].isdigit() else 1

    full_text_url: str | None = None
    full_text_urls = item.get("fullTextUrlList")
    for raw_url in _as_list(full_text_urls, "fullTextUrl"):
        if isinstance(raw_url, dict) and raw_url.get("url"):
            full_text_url = str(raw_url["url"])
            break

    publication_types = [
        str(value)
        for value in _as_list(item.get("pubTypeList"), "pubType")
        if str(value).strip()
    ]
    pmid = str(item.get("pmid") or "").strip() or None
    pmcid = str(item.get("pmcid") or "").strip() or None
    is_open_access = str(item.get("isOpenAccess") or "N").upper() == "Y"
    if is_open_access and pmcid and not full_text_url:
        full_text_url = f"https://europepmc.org/articles/{pmcid}"

    return LiteratureRecord(
        provider="europe_pmc",
        source_id=f"{source}:{external_id}",
        pmid=pmid,
        pmcid=pmcid,
        doi=normalize_doi(str(item.get("doi") or "") or None),
        title=title,
        abstract=str(item.get("abstractText") or "").strip() or None,
        journal=str(item.get("journalTitle") or "").strip() or None,
        publication_date=safe_date(publication_year, month, day),
        publication_year=publication_year,
        authors=authors,
        publication_types=publication_types,
        mesh_headings=mesh_headings,
        is_open_access=is_open_access,
        full_text_url=full_text_url,
    )


class EuropePmcProvider:
    name = "europe_pmc"

    def __init__(self, client: httpx.AsyncClient, *, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._limiter = AsyncRateLimiter(5)

    async def _search_request(self, query: str, limit: int) -> list[LiteratureRecord]:
        for attempt in range(3):
            await self._limiter.wait()
            try:
                response = await self._client.get(
                    f"{self._base_url}/search",
                    params={
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": str(limit),
                    },
                )
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise LiteratureProviderError("Europe PMC request failed") from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise LiteratureProviderError(
                        f"Europe PMC request failed with status {response.status_code}"
                    )
                await asyncio.sleep(2**attempt)
                continue
            try:
                response.raise_for_status()
                payload = response.json()
                raw_results = payload["resultList"]["result"]
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise LiteratureProviderError(
                    "Europe PMC returned an unexpected search response"
                ) from exc
            if not isinstance(raw_results, list):
                raise LiteratureProviderError("Europe PMC result list is not an array")
            records: list[LiteratureRecord] = []
            for value in raw_results:
                if isinstance(value, dict) and (record := parse_europe_pmc_result(value)):
                    records.append(record)
            return records
        raise LiteratureProviderError("Europe PMC request failed")

    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]:
        return await self._search_request(query, limit)

    async def fetch(self, source_id: str) -> LiteratureRecord:
        if ":" not in source_id:
            raise LiteratureProviderError("Europe PMC identifiers must use the SRC:ID form")
        source, external_id = source_id.split(":", 1)
        records = await self._search_request(
            f'EXT_ID:"{external_id}" AND SRC:{source.upper()}',
            1,
        )
        if not records:
            raise LiteratureRecordNotFoundError(
                f"Europe PMC record {source_id!r} was not found"
            )
        return records[0]

    async def fetch_many(self, source_ids: Sequence[str]) -> list[LiteratureRecord]:
        records: list[LiteratureRecord] = []
        for source_id in source_ids:
            records.append(await self.fetch(source_id))
        return records

    async def fetch_open_full_text_xml(self, pmcid: str) -> bytes:
        normalized = pmcid.strip().upper()
        if not normalized.startswith("PMC"):
            raise LiteratureProviderError("A PMCID is required for open full text")
        await self._limiter.wait()
        try:
            response = await self._client.get(f"{self._base_url}/{normalized}/fullTextXML")
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LiteratureRecordNotFoundError(
                    f"Open full text is unavailable for {normalized}"
                ) from exc
            raise LiteratureProviderError("Europe PMC full-text request failed") from exc
        except httpx.HTTPError as exc:
            raise LiteratureProviderError("Europe PMC full-text request failed") from exc
        if not response.content.lstrip().startswith(b"<"):
            raise LiteratureProviderError("Europe PMC returned a non-XML full-text response")
        return response.content
