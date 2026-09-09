from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.config import Settings
from science_buddy.infrastructure.models import Base, JournalMetric, Paper, PaperCitation
from science_buddy.services.scholarly_metadata import ScholarlyMetadataService


def metadata_response(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/crossref/works/" in url:
        return httpx.Response(
            200,
            json={
                "message": {
                    "publisher": "Evidence Press",
                    "type": "journal-article",
                    "container-title": ["Evidence Journal"],
                    "ISSN": ["1234-5678"],
                    "member": "https://id.crossref.org/member/1",
                    "reference-count": 1,
                    "relation": {"is-retracted-by": [{"id": "10.1000/retraction"}]},
                    "reference": [{"DOI": "https://doi.org/10.1000/TARGET"}],
                }
            },
        )
    if url.endswith("/openalex/works?filter=doi%3A10.1000%2Fsource&per-page=1"):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1000/source",
                        "cited_by_count": 42,
                        "is_retracted": False,
                        "open_access": {"is_oa": True, "oa_status": "green"},
                        "best_oa_location": {
                            "pdf_url": "https://openalex.test/paper.pdf",
                            "landing_page_url": "https://openalex.test/paper",
                        },
                        "primary_location": {
                            "source": {
                                "id": "https://openalex.org/S1",
                                "display_name": "Evidence Journal",
                                "type": "journal",
                                "issn_l": "1234-5678",
                                "issn": ["1234-5678"],
                                "is_oa": True,
                                "is_in_doaj": True,
                                "is_core": True,
                            }
                        },
                    }
                ]
            },
        )
    if "/openalex/sources/S1" in url:
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/S1",
                "display_name": "Evidence Journal",
                "issn_l": "1234-5678",
                "issn": ["1234-5678"],
                "summary_stats": {
                    "2yr_mean_citedness": 8.0,
                    "h_index": 120,
                    "i10_index": 900,
                },
                "topics": [
                    {"id": "https://openalex.org/T1", "display_name": "Evidence"}
                ],
                "updated_date": "2026-08-05T00:00:00",
            },
        )
    if "/openalex/sources?" in url:
        threshold = request.url.params["filter"]
        count = 20 if ":>8.0" in threshold else 100
        return httpx.Response(200, json={"meta": {"count": count}, "results": []})
    if "/semantic/paper/" in url:
        assert "publicationVenue" in request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "paperId": "semantic-paper-1",
                "externalIds": {"DOI": "10.1000/source"},
                "citationCount": 50,
                "influentialCitationCount": 7,
                "isOpenAccess": True,
                "openAccessPdf": {"url": "https://semantic.test/paper.pdf"},
                "venue": "Evidence Journal",
                "publicationVenue": {
                    "id": "semantic-venue-1",
                    "name": "Evidence Journal",
                    "type": "journal",
                },
                "references": [{"externalIds": {"DOI": "10.1000/target"}}],
            },
        )
    if "/unpaywall/" in url:
        assert request.url.params["email"] == "researcher@example.test"
        return httpx.Response(
            200,
            json={
                "is_oa": True,
                "oa_status": "gold",
                "genre": "journal-article",
                "best_oa_location": {
                    "url_for_pdf": "https://unpaywall.test/paper.pdf"
                },
            },
        )
    return httpx.Response(404, json={"error": f"unexpected URL: {url}"})


@pytest.mark.asyncio
async def test_metadata_fusion_persists_oa_retraction_venue_and_citation_edges(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metadata.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        _env_file=None,
        crossref_base_url="https://metadata.test/crossref",
        openalex_base_url="https://metadata.test/openalex",
        semantic_scholar_base_url="https://metadata.test/semantic",
        semantic_scholar_api_key="test-semantic-key",
        unpaywall_base_url="https://metadata.test/unpaywall",
        unpaywall_email="researcher@example.test",
    )
    transport = httpx.MockTransport(metadata_response)
    async with sessions() as session, httpx.AsyncClient(transport=transport) as client:
        source = Paper(
            title="Source paper",
            doi="10.1000/SOURCE",
            doi_normalized="10.1000/source",
            journal="Evidence Journal",
            authors=[],
            publication_types=[],
        )
        target = Paper(
            title="Target paper",
            doi="10.1000/target",
            doi_normalized="10.1000/target",
            authors=[],
            publication_types=[],
        )
        session.add_all([source, target])
        await session.commit()

        service = ScholarlyMetadataService(session, client, settings)
        first = await service.enrich_paper(source)
        await session.commit()
        second = await service.enrich_paper(source)
        await session.commit()
        citations = list((await session.scalars(select(PaperCitation))).all())
        journal_metric = await session.get(JournalMetric, source.journal_metric_id)

    assert first.sources == (
        "crossref",
        "openalex",
        "semantic_scholar",
        "unpaywall",
    )
    assert first.errors == ()
    assert first.references_linked == 1
    assert second.references_linked == 0
    assert source.is_open_access
    assert source.open_access_status == "gold"
    assert source.open_access_url == "https://unpaywall.test/paper.pdf"
    assert source.is_retracted
    assert source.retraction_status == "retracted"
    assert source.citation_count == 50
    assert source.influential_citation_count == 7
    assert source.quality_signals["journal"]["is_in_doaj"] is True
    assert source.quality_signals["journal"]["is_core"] is True
    assert source.quality_signals["journal"]["two_year_mean_citedness"] == 8.0
    assert source.quality_signals["journal"]["open_quartile"] == "OA-Q1"
    assert journal_metric is not None
    assert journal_metric.open_quartile == "OA-Q1"
    assert journal_metric.percentile == 0.8
    assert source.quality_signals["retraction_sources"] == ["crossref"]
    assert set(source.quality_signals["open_access_sources"]) == {
        "openalex",
        "semantic_scholar",
        "unpaywall",
    }
    assert len(citations) == 1
    assert citations[0].source_paper_id == source.id
    assert citations[0].target_paper_id == target.id
    assert citations[0].source_name == "metadata_fusion"
    await engine.dispose()


@pytest.mark.asyncio
async def test_metadata_fusion_reports_missing_identifiers(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metadata-missing.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session, httpx.AsyncClient(
        transport=httpx.MockTransport(metadata_response)
    ) as client:
        paper = Paper(title="No identifiers", authors=[], publication_types=[])
        session.add(paper)
        await session.commit()

        result = await ScholarlyMetadataService(
            session,
            client,
            Settings(_env_file=None),
        ).enrich_paper(paper)

    assert result.sources == ()
    assert result.errors == ("no_doi_or_pmid",)
    await engine.dispose()
