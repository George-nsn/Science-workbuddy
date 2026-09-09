import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.domain.enums import AssetSource
from science_buddy.infrastructure.models import DocumentAsset, Paper, Project, ProjectPaper
from science_buddy.services.documents import ParsedSection, TextPdfParser
from science_buddy.services.tags import TagService

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"[ \t]+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])(?:[\"'’”）)\]]*)\s+")
_ABSTRACT = re.compile(
    r"(?is)(?:^|\n)\s*(?:abstract|summary)\s*[:.—-]?\s*(.{120,5000}?)"
    r"(?=\n\s*(?:key\s*words?|keywords?|index\s+terms|(?:1\.?\s*)?introduction|"
    r"background)\b)"
)
_KEYWORDS = re.compile(
    r"(?is)(?:^|\n)\s*(?:key\s*words?|keywords?|index\s+terms)\s*[:.—-]?\s*"
    r"(.{3,700}?)(?=\n\s*(?:(?:1\.?\s*)?introduction|background)\b|\n\s*\n|$)"
)
_NOISE = re.compile(
    r"(?i)(?:https?://|www\.|doi\s*:|copyright|all rights reserved|correspondence|"
    r"e-?mail|received\s+\d|accepted\s+\d|volume\s+\d|issue\s+\d)"
)
_GENERIC_KEYWORDS = {
    "article",
    "research",
    "study",
    "method",
    "methods",
    "result",
    "results",
    "conclusion",
    "abstract",
    "introduction",
}
_DOMAIN_TERMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)anti[- ]defen[cs]e systems?"), "Anti-defense systems"),
    (re.compile(r"(?i)defen[cs]e systems?"), "Defense systems"),
    (re.compile(r"(?i)mobile genetic elements?"), "Mobile genetic elements"),
    (re.compile(r"(?i)\b(?:bacterio)?phages?\b"), "Bacteriophages"),
    (re.compile(r"(?i)CRISPR[- ]Cas"), "CRISPR-Cas"),
    (re.compile(r"(?i)machine learning"), "Machine learning"),
    (re.compile(r"(?i)deep learning"), "Deep learning"),
    (re.compile(r"(?i)bioinformatics?"), "Bioinformatics"),
    (re.compile(r"(?i)metagenom(?:e|ic|ics)"), "Metagenomics"),
    (re.compile(r"(?i)comparative genomics?"), "Comparative genomics"),
    (re.compile(r"(?i)\bprokaryot(?:e|es|ic)\b"), "Prokaryotes"),
    (re.compile(r"(?i)\bbacteri(?:a|al|um)\b"), "Bacteria"),
    (re.compile(r"(?i)\barchaea\b|\barchaeal\b"), "Archaea"),
    (re.compile(r"(?i)protein structure"), "Protein structure"),
    (re.compile(r"(?i)antimicrobial resistance"), "Antimicrobial resistance"),
    (re.compile(r"(?i)single[- ]cell"), "Single-cell analysis"),
    (re.compile(r"(?i)transcriptom(?:e|ic|ics)"), "Transcriptomics"),
    (re.compile(r"(?i)tumou?r microenvironment"), "Tumor microenvironment"),
    (re.compile(r"(?i)thyroid carcinoma"), "Thyroid carcinoma"),
)


@dataclass(frozen=True, slots=True)
class PdfEnrichment:
    summary: str
    keywords: tuple[str, ...]
    summary_kind: str = "local_extractive_pdf"


@dataclass(frozen=True, slots=True)
class PdfEnrichmentBatchResult:
    processed: int
    summaries_updated: int
    tagged: int
    failed: int


def _normalize_text(value: str) -> str:
    value = value.replace("\u00ad", "")
    value = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", value)
    lines = [_WHITESPACE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _bounded(value: str, limit: int = 1400) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    shortened = value[:limit]
    for boundary in (". ", "。", "; "):
        index = shortened.rfind(boundary, max(limit // 2, 1))
        if index > 0:
            return shortened[: index + len(boundary.strip())].strip()
    return f"{shortened.rstrip()}…"


def _front_text(sections: list[ParsedSection]) -> str:
    values: list[str] = []
    total = 0
    for section in sections[:6]:
        for segment in section.segments:
            value = _normalize_text(segment.text)
            if not value:
                continue
            values.append(value)
            total += len(value)
            if total >= 60000:
                return "\n".join(values)[:60000]
    return "\n".join(values)


def _extract_summary(text: str) -> str:
    abstract = _ABSTRACT.search(text)
    if abstract:
        return _bounded(abstract.group(1))

    candidates: list[tuple[int, int, str]] = []
    position = 0
    for sentence in _SENTENCE_BOUNDARY.split(" ".join(text[:30000].split())):
        value = sentence.strip(" \t\n-•")
        start = position
        position += len(sentence) + 1
        if len(value) < 70 or len(value) > 650 or _NOISE.search(value):
            continue
        words = re.findall(r"[A-Za-z\u3400-\u9fff]+", value)
        if len(words) < 12:
            continue
        lowered = value.casefold()
        score = 0
        score += 4 if any(
            cue in lowered
            for cue in (
                "this study",
                "we present",
                "we propose",
                "we developed",
                "we investigated",
                "we demonstrate",
                "our results",
                "the results",
                "本研究",
                "研究结果",
            )
        ) else 0
        score += 2 if 100 <= len(value) <= 380 else 0
        score += max(0, 3 - start // 6000)
        candidates.append((score, start, value))
    selected = sorted(sorted(candidates, reverse=True)[:4], key=lambda item: item[1])
    if selected:
        return _bounded(" ".join(value for _score, _start, value in selected))
    fallback = " ".join(text[:1400].split())
    return _bounded(fallback) if fallback else "PDF 未提取到可用于本地摘要的正文。"


def _clean_keyword(value: str) -> str | None:
    cleaned = re.sub(r"^[\d.()\[\]\s•-]+|[.;:。；，,\s]+$", "", value)
    cleaned = " ".join(cleaned.split())
    if not 2 <= len(cleaned) <= 80:
        return None
    if cleaned.casefold() in _GENERIC_KEYWORDS or _NOISE.search(cleaned):
        return None
    if len(cleaned.split()) > 8:
        return None
    return cleaned


def _extract_keywords(text: str) -> tuple[str, ...]:
    result: list[str] = []
    keyword_match = _KEYWORDS.search(text[:30000])
    if keyword_match:
        raw = keyword_match.group(1).replace("\n", ";")
        for value in re.split(r"[;,；，|•·]", raw):
            cleaned = _clean_keyword(value)
            if cleaned and cleaned.casefold() not in {item.casefold() for item in result}:
                result.append(cleaned)
            if len(result) >= 8:
                break
    for pattern, label in _DOMAIN_TERMS:
        if pattern.search(text) and label.casefold() not in {
            item.casefold() for item in result
        }:
            result.append(label)
        if len(result) >= 8:
            break
    return tuple(result[:8])


def extract_pdf_enrichment_from_text(text: str) -> PdfEnrichment:
    return PdfEnrichment(
        summary=_extract_summary(text),
        keywords=_extract_keywords(text),
    )


def extract_pdf_enrichment(sections: list[ParsedSection]) -> PdfEnrichment:
    return extract_pdf_enrichment_from_text(_front_text(sections))


async def enrich_project_pdfs(
    session: AsyncSession,
    *,
    project_id: UUID,
) -> PdfEnrichmentBatchResult:
    rows = (
        await session.execute(
            select(Paper.id, DocumentAsset.storage_path)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .join(DocumentAsset, DocumentAsset.paper_id == Paper.id)
            .where(
                ProjectPaper.project_id == project_id,
                DocumentAsset.source == AssetSource.USER_PDF,
            )
            .order_by(Paper.created_at, DocumentAsset.created_at)
        )
    ).all()
    seen_papers: set[UUID] = set()
    processed = summaries_updated = tagged = failed = 0
    for paper_id, storage_path in rows:
        if paper_id in seen_papers:
            continue
        seen_papers.add(paper_id)
        processed += 1
        path = Path(storage_path) if storage_path else None
        if path is None or not path.is_file():
            logger.warning("pdf_enrichment_missing_file paper_id=%s", paper_id)
            failed += 1
            continue
        try:
            paper = await session.get(Paper, paper_id)
            if paper is None:
                failed += 1
                continue
            sections = await asyncio.to_thread(TextPdfParser().parse, path)
            enrichment = extract_pdf_enrichment(sections)
            summary_changed = False
            if not paper.abstract or paper.abstract_source == "local_extractive_pdf":
                paper.abstract = enrichment.summary
                paper.abstract_source = enrichment.summary_kind
                summary_changed = True
            await TagService(session).add_auto_topic_tags(
                project_id,
                paper.id,
                list(enrichment.keywords),
            )
            await session.flush()
            await session.commit()
            summaries_updated += int(summary_changed)
            tagged += int(bool(enrichment.keywords))
        except Exception:
            await session.rollback()
            logger.exception("pdf_enrichment_failed paper_id=%s path=%s", paper_id, path)
            failed += 1
    project = await session.get(Project, project_id)
    if project is not None and (summaries_updated or tagged):
        project.retrieval_revision += 1
    await session.commit()
    return PdfEnrichmentBatchResult(
        processed=processed,
        summaries_updated=summaries_updated,
        tagged=tagged,
        failed=failed,
    )
