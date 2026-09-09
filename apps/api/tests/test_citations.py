from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.infrastructure.models import Base, Paper
from science_buddy.services.citations import (
    citation_map_for_candidates,
    format_paper_citation,
    reference_markdown,
)


def test_paper_citation_uses_stored_metadata_only() -> None:
    paper = Paper(
        title="Traceable methods study",
        journal="Methods Journal",
        publication_year=2025,
        authors=[{"full_name": "Li Ming"}, {"full_name": "Wang Wei"}],
        publication_types=[],
        doi="10.1000/example",
        doi_normalized="10.1000/example",
        pmid="12345",
    )

    label, formatted = format_paper_citation(paper)

    assert label == "Li Ming 等，2025"
    assert formatted == (
        "Li Ming, Wang Wei. Traceable methods study. Methods Journal. 2025. "
        "doi:10.1000/example; PMID:12345"
    )


@pytest.mark.asyncio
async def test_evidence_reference_keeps_citation_and_evidence_id(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'citations.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        paper = Paper(
            title="Evidence title",
            journal="Evidence Journal",
            publication_year=2024,
            authors=[{"full_name": "Zhang San"}],
            publication_types=[],
            pmid="67890",
        )
        session.add(paper)
        await session.commit()
        candidate = RetrievalCandidate(
            chunk_id=uuid4(),
            evidence_id="ev1.traceable",
            text="Evidence text",
            score=1.0,
            source_locator={},
            paper_id=paper.id,
        )
        citations = await citation_map_for_candidates(session, [candidate])

    markdown = reference_markdown([candidate.evidence_id], citations)

    assert citations[candidate.evidence_id].citation_label == "Zhang San，2024"
    assert "Evidence title" in markdown
    assert "PMID:67890" in markdown
    assert "ev1.traceable" in markdown
    await engine.dispose()