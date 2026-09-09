from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.infrastructure.models import Paper


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    evidence_id: str
    citation_label: str
    formatted_citation: str


def _author_text(paper: Paper) -> tuple[str, str]:
    names = [
        str(author.get("full_name") or "").strip()
        for author in paper.authors
        if isinstance(author, dict) and author.get("full_name")
    ]
    if not names:
        return "作者不详", "作者不详"
    first = names[0]
    inline = f"{first} 等" if len(names) > 1 else first
    bibliography = ", ".join(names[:6])
    if len(names) > 6:
        bibliography += ", et al."
    return inline, bibliography


def format_paper_citation(paper: Paper) -> tuple[str, str]:
    inline_author, bibliography_authors = _author_text(paper)
    year = str(paper.publication_year) if paper.publication_year else "年份不详"
    label = f"{inline_author}，{year}"
    source = paper.journal or "来源不详"
    identifiers = []
    if paper.doi_normalized or paper.doi:
        identifiers.append(f"doi:{paper.doi_normalized or paper.doi}")
    if paper.pmid:
        identifiers.append(f"PMID:{paper.pmid}")
    if paper.pmcid:
        identifiers.append(f"PMCID:{paper.pmcid}")
    suffix = f" {'; '.join(identifiers)}" if identifiers else ""
    return label, f"{bibliography_authors}. {paper.title}. {source}. {year}.{suffix}".strip()


async def citation_map_for_candidates(
    session: AsyncSession,
    candidates: list[RetrievalCandidate],
) -> dict[str, EvidenceCitation]:
    paper_ids = {candidate.paper_id for candidate in candidates if candidate.paper_id}
    papers = (
        list((await session.scalars(select(Paper).where(Paper.id.in_(paper_ids)))).all())
        if paper_ids
        else []
    )
    by_id: dict[UUID, Paper] = {paper.id: paper for paper in papers}
    result: dict[str, EvidenceCitation] = {}
    for candidate in candidates:
        paper = by_id.get(candidate.paper_id) if candidate.paper_id else None
        if paper is None:
            continue
        label, formatted = format_paper_citation(paper)
        result[candidate.evidence_id] = EvidenceCitation(
            evidence_id=candidate.evidence_id,
            citation_label=label,
            formatted_citation=formatted,
        )
    return result


def reference_markdown(
    evidence_ids: list[str],
    citations: dict[str, EvidenceCitation],
) -> str:
    unique_ids = list(dict.fromkeys(evidence_ids))
    rows = [citations[value] for value in unique_ids if value in citations]
    if not rows:
        return ""
    lines = ["## 参考文献"]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. {row.formatted_citation} `{row.evidence_id}`"
        )
    return "\n".join(lines)