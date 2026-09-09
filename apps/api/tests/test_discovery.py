from collections.abc import Sequence

import pytest

from science_buddy.domain.providers import LiteratureRecord
from science_buddy.services.literature.discovery import (
    LiteratureDiscoveryService,
    interleave_literature_batches,
)


class FakeProvider:
    def __init__(self, name: str, records: list[LiteratureRecord]) -> None:
        self.name = name
        self.records = records

    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]:
        return self.records[:limit]

    async def fetch(self, source_id: str) -> LiteratureRecord:
        return next(record for record in self.records if record.source_id == source_id)

    async def fetch_many(self, source_ids: Sequence[str]) -> list[LiteratureRecord]:
        return [record for record in self.records if record.source_id in source_ids]


class FailingProvider(FakeProvider):
    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]:
        raise RuntimeError("provider unavailable")


@pytest.mark.asyncio
async def test_discovery_deduplicates_pubmed_and_europe_pmc_by_pmid() -> None:
    pubmed = LiteratureRecord(
        provider="pubmed",
        source_id="123",
        pmid="123",
        title="A traceable paper",
        abstract="Short abstract.",
    )
    europe = LiteratureRecord(
        provider="europe_pmc",
        source_id="MED:123",
        pmid="123",
        pmcid="PMC123",
        title="A traceable paper",
        abstract="A longer and more complete abstract.",
        is_open_access=True,
    )
    service = LiteratureDiscoveryService(
        [FakeProvider("pubmed", [pubmed]), FakeProvider("europe_pmc", [europe])]
    )

    results = await service.search(
        "traceable",
        provider_names=["pubmed", "europe_pmc"],
        limit=10,
    )

    assert len(results) == 1
    assert results[0].pmcid == "PMC123"
    assert results[0].abstract == "A longer and more complete abstract."
    assert {source.provider for source in results[0].sources} == {
        "pubmed",
        "europe_pmc",
    }


@pytest.mark.asyncio
async def test_discovery_can_tolerate_one_provider_failure() -> None:
    record = LiteratureRecord(
        provider="openalex",
        source_id="W1",
        title="Partial success",
    )
    service = LiteratureDiscoveryService(
        [FakeProvider("openalex", [record]), FailingProvider("crossref", [])]
    )

    results = await service.search(
        "traceable",
        provider_names=["openalex", "crossref"],
        limit=10,
        tolerate_failures=True,
    )

    assert [result.title for result in results] == ["Partial success"]


def test_discovery_interleaves_provider_batches() -> None:
    pubmed = [
        LiteratureRecord(provider="pubmed", source_id=str(index), title=f"P{index}")
        for index in range(3)
    ]
    openalex = [
        LiteratureRecord(provider="openalex", source_id=f"W{index}", title=f"O{index}")
        for index in range(2)
    ]

    values = interleave_literature_batches([pubmed, openalex])

    assert [value.title for value in values] == ["P0", "O0", "P1", "O1", "P2"]
