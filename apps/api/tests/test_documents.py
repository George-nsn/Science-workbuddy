from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.contracts import SourceSegment
from science_buddy.domain.enums import AssetSource, EvidenceDepth
from science_buddy.infrastructure.models import Base, Chunk, Paper
from science_buddy.services.document_ingestion import DocumentIngestionService
from science_buddy.services.documents import (
    DoclingPdfParser,
    DocumentParseError,
    EuropePmcXmlParser,
    FallbackPdfParser,
    GrobidPdfParser,
    ParsedSection,
    TextPdfParser,
)


class FakeDoclingDocument:
    def export_to_markdown(self) -> str:
        return "# Abstract\n\nDocling extracted evidence.\n\n## Results\n\nA structured result."


class FakeDoclingResult:
    document = FakeDoclingDocument()


class FakeDoclingConverter:
    def convert(self, _path: str) -> FakeDoclingResult:
        return FakeDoclingResult()


class FailingPdfParser:
    parser_version = "failing-v1"
    extraction_metadata: dict[str, object] = {"backend": "failing"}

    def parse(self, _path: Path) -> list[ParsedSection]:
        raise DocumentParseError("backend unavailable")


class SuccessfulPdfParser:
    parser_version = "successful-v1"
    extraction_metadata: dict[str, object] = {
        "backend": "successful",
        "content_origin": "parser_derived_text",
    }

    def parse(self, _path: Path) -> list[ParsedSection]:
        text = "Fallback evidence"
        return [
            ParsedSection(
                "Results",
                "Results",
                0,
                (SourceSegment(text, "Results", None, None, 0, len(text)),),
            )
        ]


def test_europe_pmc_xml_parser_preserves_section_hierarchy() -> None:
    payload = b"""<?xml version='1.0'?>
    <article><front><article-meta><abstract><p>Structured abstract evidence.</p></abstract>
    </article-meta></front><body><sec><title>Results</title>
    <p>BRAF V600E was observed in the study cohort.</p>
    <sec><title>Subgroup analysis</title><p>The association varied by subgroup.</p></sec>
    </sec></body></article>"""

    sections = EuropePmcXmlParser().parse(payload)

    assert [section.section_path for section in sections] == [
        "Abstract",
        "Results",
        "Results > Subgroup analysis",
    ]
    assert sections[1].segments[0].text.startswith("BRAF V600E")


def test_text_pdf_parser_extracts_page_locations(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Traceable biomedical evidence from a text based PDF. " * 8,
    )
    document.save(path)
    document.close()

    sections = TextPdfParser().parse(path)

    assert len(sections) == 1
    assert sections[0].section_path == "Page 1"
    assert sections[0].segments[0].page_start == 1
    assert "Traceable biomedical evidence" in sections[0].segments[0].text


def test_text_pdf_parser_rejects_image_only_pdf(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(path)
    document.close()

    with pytest.raises(DocumentParseError, match="OCR"):
        TextPdfParser().parse(path)


def test_docling_parser_converts_markdown_to_hierarchical_sections(tmp_path: Path) -> None:
    path = tmp_path / "layout.pdf"
    path.write_bytes(b"%PDF-fake")

    sections = DoclingPdfParser(lambda: FakeDoclingConverter()).parse(path)

    assert [section.section_path for section in sections] == [
        "Abstract",
        "Abstract > Results",
    ]
    assert sections[1].segments[0].text == "A structured result."


def test_grobid_tei_parser_retains_body_sections() -> None:
    payload = b"""<?xml version='1.0'?>
    <TEI xmlns='http://www.tei-c.org/ns/1.0'><text><body>
      <div><head>Methods</head><p>Cells were cultured locally.</p></div>
      <div><head>Results</head><p>BRAF expression increased.</p>
      <figure><figDesc>Figure one description.</figDesc></figure></div>
    </body></text></TEI>"""

    sections = GrobidPdfParser._parse_tei(payload)

    assert [section.title for section in sections] == ["Methods", "Results"]
    assert "Figure one description" in sections[1].segments[0].text


def test_parser_fallback_records_failures_and_raw_asset_provenance(tmp_path: Path) -> None:
    parser = FallbackPdfParser([FailingPdfParser(), SuccessfulPdfParser()])

    sections = parser.parse(tmp_path / "paper.pdf")

    assert sections[0].segments[0].text == "Fallback evidence"
    assert parser.parser_version == "successful-v1"
    assert parser.extraction_metadata["backend"] == "successful"
    assert parser.extraction_metadata["raw_document_preserved"] is True
    assert parser.extraction_metadata["model_derived_description"] is False
    assert parser.extraction_metadata["fallback_failures"] == [
        "failing-v1:backend unavailable"
    ]


@pytest.mark.asyncio
async def test_document_ingestion_deduplicates_identical_chunks_in_section(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chunks.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repeated = "Repeated table or page header text."
    async with sessions() as session:
        paper = Paper(title="Repeated PDF elements", authors=[], publication_types=[])
        session.add(paper)
        await session.flush()
        asset, chunks_created = await DocumentIngestionService(session).ingest_sections(
            paper=paper,
            source=AssetSource.USER_PDF,
            evidence_depth=EvidenceDepth.USER_PDF,
            content=b"deduplication-test-pdf",
            sections=[
                ParsedSection(
                    section_path="Page 4",
                    title="Page 4",
                    ordinal=0,
                    segments=(
                        SourceSegment(repeated, "Page 4", 4, 4, 0, len(repeated)),
                        SourceSegment(repeated, "Page 4", 4, 4, 100, 100 + len(repeated)),
                    ),
                )
            ],
            media_type="application/pdf",
            parser_version="test",
        )
        await session.commit()
        chunks = list(
            (
                await session.scalars(select(Chunk))
            ).all()
        )

    assert asset.parse_status.value == "ready"
    assert chunks_created == 1
    assert len(chunks) == 1
    assert chunks[0].text == repeated
    assert chunks[0].ordinal == 0
    await engine.dispose()
