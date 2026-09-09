import importlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import pymupdf

from science_buddy.domain.contracts import SourceSegment


class DocumentParseError(ValueError):
    """Raised when a user document cannot be safely parsed into text sections."""


@dataclass(frozen=True, slots=True)
class ParsedSection:
    title: str | None
    section_path: str
    ordinal: int
    segments: tuple[SourceSegment, ...]


class PdfParser(Protocol):
    parser_version: str
    extraction_metadata: dict[str, object]

    def parse(self, path: Path) -> list[ParsedSection]: ...


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


class EuropePmcXmlParser:
    """Parse JATS-like Europe PMC XML while retaining section hierarchy."""

    def parse(self, payload: bytes) -> list[ParsedSection]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise DocumentParseError("Europe PMC full text is not valid XML") from exc

        parsed: list[ParsedSection] = []
        ordinal = 0
        abstract_nodes = [node for node in root.iter() if _local_name(node.tag) == "abstract"]
        if abstract_nodes:
            segments = self._paragraph_segments(abstract_nodes[0], "Abstract")
            if segments:
                parsed.append(ParsedSection("Abstract", "Abstract", ordinal, tuple(segments)))
                ordinal += 1

        body = next((node for node in root.iter() if _local_name(node.tag) == "body"), None)
        if body is None:
            if parsed:
                return parsed
            raise DocumentParseError("Europe PMC XML does not contain an article body")

        def visit(section: ET.Element, parents: list[str]) -> None:
            nonlocal ordinal
            title_node = next(
                (child for child in section if _local_name(child.tag) == "title"),
                None,
            )
            title = (
                _element_text(title_node)
                if title_node is not None
                else f"Section {ordinal + 1}"
            )
            path = " > ".join([*parents, title])
            segments = self._paragraph_segments(section, path, direct_only=True)
            if segments:
                parsed.append(ParsedSection(title, path, ordinal, tuple(segments)))
                ordinal += 1
            for child in section:
                if _local_name(child.tag) == "sec":
                    visit(child, [*parents, title])

        top_sections = [child for child in body if _local_name(child.tag) == "sec"]
        if top_sections:
            for section in top_sections:
                visit(section, [])
        else:
            segments = self._paragraph_segments(body, "Body")
            if segments:
                parsed.append(ParsedSection("Body", "Body", ordinal, tuple(segments)))
        if not parsed:
            raise DocumentParseError("Europe PMC XML contains no usable article text")
        return parsed

    @staticmethod
    def _paragraph_segments(
        element: ET.Element, section_path: str, *, direct_only: bool = False
    ) -> list[SourceSegment]:
        if direct_only:
            candidates = [
                child
                for child in element
                if _local_name(child.tag) in {"p", "list", "boxed-text"}
            ]
        else:
            candidates = [node for node in element.iter() if _local_name(node.tag) == "p"]
        segments: list[SourceSegment] = []
        cursor = 0
        for candidate in candidates:
            text = _element_text(candidate)
            if not text:
                continue
            segments.append(
                SourceSegment(
                    text=text,
                    section_path=section_path,
                    page_start=None,
                    page_end=None,
                    char_start=cursor,
                    char_end=cursor + len(text),
                )
            )
            cursor += len(text) + 2
        return segments


class TextPdfParser:
    """Extract text blocks and page positions from text-based PDFs only."""

    parser_version = "pymupdf-v1"
    extraction_metadata: dict[str, object] = {
        "backend": "pymupdf",
        "content_origin": "deterministic_extraction",
        "layout_features": ["page_blocks"],
    }

    def parse(self, path: Path) -> list[ParsedSection]:
        try:
            document: Any = pymupdf.open(path)  # type: ignore[no-untyped-call]
        except Exception as exc:
            raise DocumentParseError("PDF could not be opened safely") from exc
        try:
            if document.page_count == 0:
                raise DocumentParseError("PDF contains no pages")
            sections: list[ParsedSection] = []
            ordinal = 0
            total_characters = 0
            for page_index, page in enumerate(document):
                blocks = sorted(
                    page.get_text("blocks"),
                    key=lambda block: (round(float(block[1]), 1), float(block[0])),
                )
                segments: list[SourceSegment] = []
                page_cursor = 0
                for block in blocks:
                    text = re.sub(r"[ \t]+", " ", str(block[4])).strip()
                    text = re.sub(r"\n{3,}", "\n\n", text)
                    if len(text) < 2:
                        continue
                    total_characters += len(text)
                    segments.append(
                        SourceSegment(
                            text=text,
                            section_path=f"Page {page_index + 1}",
                            page_start=page_index + 1,
                            page_end=page_index + 1,
                            char_start=page_cursor,
                            char_end=page_cursor + len(text),
                        )
                    )
                    page_cursor += len(text) + 2
                if segments:
                    sections.append(
                        ParsedSection(
                            title=f"Page {page_index + 1}",
                            section_path=f"Page {page_index + 1}",
                            ordinal=ordinal,
                            segments=tuple(segments),
                        )
                    )
                    ordinal += 1
            if total_characters < max(100, document.page_count * 20):
                raise DocumentParseError(
                    "PDF contains too little extractable text and may require OCR"
                )
            return sections
        finally:
            document.close()


def _markdown_sections(markdown: str, *, backend: str) -> list[ParsedSection]:
    text = markdown.strip()
    if not text:
        raise DocumentParseError(f"{backend} produced no usable document text")
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    sections: list[ParsedSection] = []
    stack: dict[int, str] = {}
    current_title = "Document"
    current_path = "Document"
    current_lines: list[str] = []

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if not content:
            return
        sections.append(
            ParsedSection(
                current_title,
                current_path,
                len(sections),
                (
                    SourceSegment(
                        text=content,
                        section_path=current_path,
                        page_start=None,
                        page_end=None,
                        char_start=0,
                        char_end=len(content),
                    ),
                ),
            )
        )

    for line in text.splitlines():
        heading = heading_pattern.match(line)
        if not heading:
            current_lines.append(line)
            continue
        flush()
        current_lines = []
        level = len(heading.group(1))
        current_title = heading.group(2).strip()
        stack[level] = current_title
        for deeper in range(level + 1, 7):
            stack.pop(deeper, None)
        current_path = " > ".join(stack[index] for index in sorted(stack) if index <= level)
    flush()
    if not sections:
        raise DocumentParseError(f"{backend} produced no usable sections")
    return sections


class DoclingPdfParser:
    """Optional local layout parser for text, tables, figures and OCR-capable documents."""

    parser_version = "docling-v2"
    extraction_metadata: dict[str, object] = {
        "backend": "docling",
        "content_origin": "parser_derived_text",
        "layout_features": ["headings", "tables", "figures", "reading_order", "ocr"],
    }

    def __init__(self, converter_factory: Callable[[], Any] | None = None) -> None:
        self._converter_factory = converter_factory

    def parse(self, path: Path) -> list[ParsedSection]:
        factory = self._converter_factory
        if factory is None:
            try:
                module = importlib.import_module("docling.document_converter")
            except ImportError as exc:
                raise DocumentParseError(
                    "Docling is not installed; install the 'docling' optional dependency"
                ) from exc
            factory = module.DocumentConverter
        try:
            result = factory().convert(str(path))
            markdown = result.document.export_to_markdown()
        except Exception as exc:
            raise DocumentParseError("Docling could not parse the PDF") from exc
        return _markdown_sections(str(markdown), backend="Docling")


class GrobidPdfParser:
    """Optional GROBID TEI parser; the configured service is expected to run locally."""

    parser_version = "grobid-tei-v1"
    extraction_metadata: dict[str, object] = {
        "backend": "grobid",
        "content_origin": "parser_derived_text",
        "layout_features": ["tei_sections", "bibliography", "reading_order"],
    }

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def parse(self, path: Path) -> list[ParsedSection]:
        try:
            with path.open("rb") as handle, httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/api/processFulltextDocument",
                    files={"input": (path.name, handle, "application/pdf")},
                    data={"consolidateHeader": "1", "consolidateCitations": "0"},
                )
                response.raise_for_status()
        except (OSError, httpx.HTTPError) as exc:
            raise DocumentParseError("GROBID could not parse the PDF") from exc
        return self._parse_tei(response.content)

    @staticmethod
    def _parse_tei(payload: bytes) -> list[ParsedSection]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise DocumentParseError("GROBID returned invalid TEI XML") from exc
        sections: list[ParsedSection] = []
        for div in (node for node in root.iter() if _local_name(node.tag) == "div"):
            head = next(
                (child for child in div if _local_name(child.tag) == "head"),
                None,
            )
            title = _element_text(head) if head is not None else f"Section {len(sections) + 1}"
            blocks = [
                _element_text(child)
                for child in div
                if _local_name(child.tag) in {"p", "figure", "table"}
            ]
            text = "\n\n".join(value for value in blocks if value)
            if not text:
                continue
            sections.append(
                ParsedSection(
                    title,
                    title,
                    len(sections),
                    (
                        SourceSegment(
                            text=text,
                            section_path=title,
                            page_start=None,
                            page_end=None,
                            char_start=0,
                            char_end=len(text),
                        ),
                    ),
                )
            )
        if not sections:
            raise DocumentParseError("GROBID TEI contains no usable body sections")
        return sections


class FallbackPdfParser:
    """Try configured parsers in order while exposing the backend that succeeded."""

    def __init__(self, parsers: Sequence[PdfParser]) -> None:
        if not parsers:
            raise ValueError("At least one PDF parser is required")
        self._parsers = tuple(parsers)
        self.parser_version = parsers[-1].parser_version
        self.extraction_metadata: dict[str, object] = {}

    def parse(self, path: Path) -> list[ParsedSection]:
        failures: list[str] = []
        for parser in self._parsers:
            try:
                sections = parser.parse(path)
            except DocumentParseError as exc:
                failures.append(f"{parser.parser_version}:{exc}")
                continue
            self.parser_version = parser.parser_version
            self.extraction_metadata = {
                **parser.extraction_metadata,
                "fallback_failures": failures,
                "raw_document_preserved": True,
                "model_derived_description": False,
            }
            return sections
        raise DocumentParseError("; ".join(failures))


def build_pdf_parser(
    *,
    backend: str,
    grobid_base_url: str,
    timeout_seconds: float,
) -> FallbackPdfParser:
    text = TextPdfParser()
    docling = DoclingPdfParser()
    grobid = GrobidPdfParser(
        base_url=grobid_base_url,
        timeout_seconds=timeout_seconds,
    )
    parsers: dict[str, Sequence[PdfParser]] = {
        "pymupdf": (text,),
        "docling": (docling, text),
        "grobid": (grobid, docling, text),
        "auto": (docling, text),
    }
    if backend not in parsers:
        raise ValueError("PDF parser backend must be auto, pymupdf, docling, or grobid")
    return FallbackPdfParser(parsers[backend])
