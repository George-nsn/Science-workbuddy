from pathlib import Path

import httpx
import pytest

from science_buddy.services.literature.crossref import (
    CrossrefProvider,
    parse_crossref_work,
)
from science_buddy.services.literature.europe_pmc import (
    EuropePmcProvider,
    parse_europe_pmc_result,
)
from science_buddy.services.literature.openalex import (
    OpenAlexProvider,
    parse_openalex_work,
)
from science_buddy.services.literature.pubmed import PubMedProvider, parse_pubmed_xml

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_pubmed_xml_preserves_identifiers_and_mesh() -> None:
    records = parse_pubmed_xml((FIXTURES / "pubmed_sample.xml").read_text(encoding="utf-8"))

    assert len(records) == 1
    record = records[0]
    assert record.pmid == "12345678"
    assert record.pmcid == "PMC1234567"
    assert record.doi == "10.1000/trace.001"
    assert record.publication_year == 2025
    assert record.authors[0].full_name == "Ming Li"
    assert record.mesh_headings[0].descriptor_ui == "D013964"
    assert record.mesh_headings[0].is_major_topic
    assert record.abstract and "BACKGROUND:" in record.abstract


def test_parse_europe_pmc_core_result() -> None:
    record = parse_europe_pmc_result(
        {
            "source": "MED",
            "id": "12345678",
            "pmid": "12345678",
            "pmcid": "PMC1234567",
            "doi": "10.1000/TRACE.001",
            "title": "BRAF V600E and outcomes",
            "abstractText": "The association varied across subgroups.",
            "pubYear": "2025",
            "firstPublicationDate": "2025-03-12",
            "isOpenAccess": "Y",
            "authorList": {"author": [{"fullName": "Ming Li", "lastName": "Li"}]},
            "pubTypeList": {"pubType": ["Journal Article"]},
        }
    )

    assert record is not None
    assert record.source_id == "MED:12345678"
    assert record.doi == "10.1000/trace.001"
    assert record.is_open_access
    assert record.full_text_url == "https://europepmc.org/articles/PMC1234567"


def test_parse_openalex_and_crossref_records() -> None:
    openalex = parse_openalex_work(
        {
            "id": "https://openalex.org/W1",
            "display_name": "Traceable article",
            "doi": "https://doi.org/10.1000/TRACE.001",
            "publication_year": 2025,
            "abstract_inverted_index": {"Traceable": [0], "abstract": [1]},
            "primary_location": {
                "source": {"display_name": "Evidence Journal"}
            },
            "open_access": {"is_oa": True, "oa_status": "gold"},
            "cited_by_count": 12,
        }
    )
    crossref = parse_crossref_work(
        {
            "DOI": "10.1000/TRACE.001",
            "title": ["Traceable article"],
            "container-title": ["Evidence Journal"],
            "published-online": {"date-parts": [[2025, 3, 12]]},
            "author": [{"given": "Ming", "family": "Li"}],
            "is-referenced-by-count": 10,
        }
    )

    assert openalex is not None and openalex.abstract == "Traceable abstract"
    assert openalex.doi == "10.1000/trace.001"
    assert openalex.citation_count == 12
    assert crossref is not None and crossref.publication_year == 2025
    assert crossref.authors[0].full_name == "Ming Li"


@pytest.mark.asyncio
async def test_pubmed_provider_searches_then_fetches() -> None:
    xml_payload = (FIXTURES / "pubmed_sample.xml").read_text(encoding="utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["tool"] == "science_buddy_test"
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["12345678"]}},
            )
        return httpx.Response(200, text=xml_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PubMedProvider(
            client,
            base_url="https://example.test/eutils",
            tool="science_buddy_test",
        )
        records = await provider.search("BRAF", limit=5)

    assert [record.pmid for record in records] == ["12345678"]


@pytest.mark.asyncio
async def test_europe_pmc_provider_uses_core_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["resultType"] == "core"
        return httpx.Response(
            200,
            json={
                "resultList": {
                    "result": [
                        {
                            "source": "MED",
                            "id": "12345678",
                            "pmid": "12345678",
                            "title": "Traceable article",
                            "pubYear": "2025",
                        }
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = EuropePmcProvider(client, base_url="https://example.test/rest")
        records = await provider.search("BRAF", limit=5)

    assert len(records) == 1
    assert records[0].provider == "europe_pmc"


@pytest.mark.asyncio
async def test_openalex_and_crossref_providers_search() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openalex.test":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "display_name": "OpenAlex result",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {"DOI": "10.1000/crossref", "title": ["Crossref result"]}
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        openalex = await OpenAlexProvider(
            client, base_url="https://openalex.test"
        ).search("phage", limit=5)
        crossref = await CrossrefProvider(
            client, base_url="https://crossref.test"
        ).search("phage", limit=5)

    assert [record.source_id for record in openalex] == ["W1"]
    assert [record.doi for record in crossref] == ["10.1000/crossref"]
