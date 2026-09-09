import asyncio
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date

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

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _publication_date(article: ET.Element) -> date | None:
    nodes = [
        article.find("./MedlineCitation/Article/ArticleDate"),
        article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate"),
    ]
    for node in nodes:
        if node is None:
            continue
        year = parse_year(_text(node.find("Year")) or _text(node.find("MedlineDate")))
        month_text = (_text(node.find("Month")) or "1").strip().lower()
        month = int(month_text) if month_text.isdigit() else _MONTHS.get(month_text[:3], 1)
        day_text = _text(node.find("Day")) or "1"
        day = int(day_text) if day_text.isdigit() else 1
        parsed = safe_date(year, month, day)
        if parsed:
            return parsed
    return None


def _authors(article: ET.Element) -> list[AuthorRecord]:
    records: list[AuthorRecord] = []
    for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
        collective = _text(author.find("CollectiveName"))
        family = _text(author.find("LastName"))
        given = _text(author.find("ForeName"))
        full_name = collective or " ".join(value for value in (given, family) if value)
        if full_name:
            records.append(
                AuthorRecord(
                    full_name=full_name,
                    family_name=family,
                    given_name=given,
                    collective_name=collective,
                )
            )
    return records


def _abstract(article: ET.Element) -> str | None:
    parts: list[str] = []
    for item in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
        value = _text(item)
        if not value:
            continue
        label = item.attrib.get("Label")
        parts.append(f"{label}: {value}" if label else value)
    return "\n\n".join(parts) or None


def _article_ids(article: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        value = _text(item)
        kind = item.attrib.get("IdType", "").lower()
        if value and kind:
            values[kind] = value
    return values


def parse_pubmed_xml(payload: str) -> list[LiteratureRecord]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise LiteratureProviderError("PubMed returned malformed XML") from exc

    records: list[LiteratureRecord] = []
    for article in root.findall("./PubmedArticle"):
        pmid = _text(article.find("./MedlineCitation/PMID"))
        title = _text(article.find("./MedlineCitation/Article/ArticleTitle"))
        if not pmid or not title:
            continue
        ids = _article_ids(article)
        published = _publication_date(article)
        mesh_headings: list[MeSHHeadingRecord] = []
        for heading in article.findall("./MedlineCitation/MeshHeadingList/MeshHeading"):
            descriptor = heading.find("DescriptorName")
            if descriptor is None:
                continue
            ui = descriptor.attrib.get("UI")
            label = _text(descriptor)
            if ui and label:
                mesh_headings.append(
                    MeSHHeadingRecord(
                        descriptor_ui=ui,
                        label=label,
                        is_major_topic=descriptor.attrib.get("MajorTopicYN") == "Y",
                    )
                )
        records.append(
            LiteratureRecord(
                provider="pubmed",
                source_id=pmid,
                pmid=pmid,
                pmcid=ids.get("pmc"),
                doi=normalize_doi(ids.get("doi")),
                title=title,
                abstract=_abstract(article),
                journal=_text(article.find("./MedlineCitation/Article/Journal/Title")),
                publication_date=published,
                publication_year=published.year if published else None,
                authors=_authors(article),
                publication_types=[
                    value
                    for node in article.findall(
                        "./MedlineCitation/Article/PublicationTypeList/PublicationType"
                    )
                    if (value := _text(node))
                ],
                mesh_headings=mesh_headings,
                is_open_access=bool(ids.get("pmc")),
                full_text_url=(
                    f"https://europepmc.org/articles/{ids['pmc']}" if ids.get("pmc") else None
                ),
            )
        )
    return records


class PubMedProvider:
    name = "pubmed"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        tool: str,
        email: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._tool = tool
        self._email = email
        self._api_key = api_key
        self._limiter = AsyncRateLimiter(10 if api_key else 3)

    def _common_params(self) -> dict[str, str]:
        params = {"tool": self._tool}
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def _request(self, endpoint: str, params: dict[str, str]) -> httpx.Response:
        for attempt in range(3):
            await self._limiter.wait()
            try:
                response = await self._client.get(
                    f"{self._base_url}/{endpoint}",
                    params={**self._common_params(), **params},
                )
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise LiteratureProviderError("PubMed request failed") from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise LiteratureProviderError(
                        f"PubMed request failed with status {response.status_code}"
                    )
                await asyncio.sleep(2**attempt)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LiteratureProviderError(
                    f"PubMed rejected the request with status {response.status_code}"
                ) from exc
            return response
        raise LiteratureProviderError("PubMed request failed")

    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]:
        response = await self._request(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": query,
                "retmode": "json",
                "retmax": str(limit),
                "sort": "relevance",
            },
        )
        try:
            identifiers = response.json()["esearchresult"]["idlist"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LiteratureProviderError("PubMed returned an unexpected search response") from exc
        return await self.fetch_many(identifiers)

    async def fetch(self, source_id: str) -> LiteratureRecord:
        records = await self.fetch_many([source_id])
        if not records:
            raise LiteratureRecordNotFoundError(f"PubMed record {source_id!r} was not found")
        return records[0]

    async def fetch_many(self, source_ids: Sequence[str]) -> list[LiteratureRecord]:
        identifiers = [value.strip() for value in source_ids if value.strip()]
        if not identifiers:
            return []
        if any(not re.fullmatch(r"\d+", value) for value in identifiers):
            raise LiteratureProviderError("PubMed identifiers must contain digits only")
        response = await self._request(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(identifiers),
                "retmode": "xml",
            },
        )
        return parse_pubmed_xml(response.text)
