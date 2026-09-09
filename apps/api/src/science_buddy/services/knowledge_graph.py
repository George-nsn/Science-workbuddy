import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    Chunk,
    Claim,
    DocumentAsset,
    EvidenceLink,
    GraphEdge,
    GraphNode,
    MeSHTerm,
    Paper,
    PaperCitation,
    PaperMeSH,
    PaperTag,
    Project,
    ProjectFact,
    ProjectPaper,
    Section,
    Tag,
)
from science_buddy.services.graphrag import FullGraphRagIndexer


@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    project_id: UUID
    nodes: int
    edges: int
    revision: int
    scope_key: str
    communities: int = 0
    reports: int = 0
    mentions: int = 0


class KnowledgeGraphBuilder:
    """Build a deterministic, source-grounded project graph without LLM triples."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rebuild(
        self,
        project_id: UUID,
        *,
        paper_ids: set[UUID] | None = None,
        scope_key: str = "project",
    ) -> GraphBuildResult:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError("Project does not exist")
        await self._session.execute(
            delete(GraphEdge).where(
                GraphEdge.project_id == project_id,
                GraphEdge.scope_key == scope_key,
            )
        )
        await self._session.execute(
            delete(GraphNode).where(
                GraphNode.project_id == project_id,
                GraphNode.scope_key == scope_key,
            )
        )

        paper_statement = (
            select(Paper)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(ProjectPaper.project_id == project_id)
        )
        if paper_ids is not None:
            paper_statement = paper_statement.where(Paper.id.in_(paper_ids))
        papers = list((await self._session.scalars(paper_statement)).all())
        selected_paper_ids = {paper.id for paper in papers}
        if not selected_paper_ids:
            raise ValueError("The graph scope contains no project papers")
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        def add_node(key: str, node_type: str, label: str, properties: dict[str, object]) -> None:
            nodes.setdefault(
                key,
                GraphNode(
                    project_id=project_id,
                    scope_key=scope_key,
                    node_key=key,
                    node_type=node_type,
                    label=label,
                    properties=properties,
                ),
            )

        for paper in papers:
            add_node(
                f"paper:{paper.id}",
                "paper",
                paper.title,
                {
                    "pmid": paper.pmid,
                    "pmcid": paper.pmcid,
                    "doi": paper.doi_normalized,
                    "publication_year": paper.publication_year,
                    "journal": paper.journal,
                    "is_retracted": paper.is_retracted,
                    "citation_count": paper.citation_count,
                },
            )
            for author in paper.authors:
                name = str(
                    author.get("full_name")
                    or author.get("collective_name")
                    or ""
                ).strip()
                if not name:
                    continue
                author_key = self._entity_key("author", name)
                add_node(
                    author_key,
                    "author",
                    name,
                    {"normalized_name": self._normalize_entity(name)},
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        scope_key=scope_key,
                        source_key=f"paper:{paper.id}",
                        relation="AUTHORED_BY",
                        target_key=author_key,
                        evidence_chunk_id=None,
                        evidence_key="",
                        provenance="paper_authors",
                        confidence=1.0,
                    )
                )
            if paper.journal and paper.journal.strip():
                journal_key = self._entity_key("journal", paper.journal)
                add_node(
                    journal_key,
                    "journal",
                    paper.journal.strip(),
                    {
                        "normalized_name": self._normalize_entity(paper.journal),
                        "quality_signals": paper.quality_signals.get("journal", {}),
                    },
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        scope_key=scope_key,
                        source_key=f"paper:{paper.id}",
                        relation="PUBLISHED_IN",
                        target_key=journal_key,
                        evidence_chunk_id=None,
                        evidence_key="",
                        provenance="paper_metadata",
                        confidence=1.0,
                    )
                )

        chunk_rows = (
            await self._session.execute(
                select(Chunk, DocumentAsset.paper_id)
                .join(Section, Chunk.section_id == Section.id)
                .join(DocumentAsset, Section.asset_id == DocumentAsset.id)
                .join(ProjectPaper, ProjectPaper.paper_id == DocumentAsset.paper_id)
                .where(
                    ProjectPaper.project_id == project_id,
                    DocumentAsset.paper_id.in_(selected_paper_ids),
                )
            )
        ).all()
        for chunk, paper_id in chunk_rows:
            chunk_key = f"chunk:{chunk.id}"
            add_node(
                chunk_key,
                "chunk",
                chunk.text[:200],
                {
                    "section": chunk.source_locator.get("section_path"),
                    "paper_id": str(paper_id),
                    "content_hash": chunk.content_hash,
                },
            )
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    scope_key=scope_key,
                    source_key=f"paper:{paper_id}",
                    relation="HAS_CHUNK",
                    target_key=chunk_key,
                    evidence_chunk_id=chunk.id,
                    evidence_key=str(chunk.id),
                    provenance="document_structure",
                    confidence=1.0,
                )
            )

        mesh_rows = (
            await self._session.execute(
                select(PaperMeSH, MeSHTerm)
                .join(MeSHTerm, MeSHTerm.descriptor_ui == PaperMeSH.descriptor_ui)
                .where(PaperMeSH.paper_id.in_(selected_paper_ids))
            )
        ).all()
        for mapping, term in mesh_rows:
            mesh_key = f"mesh:{term.descriptor_ui}"
            add_node(
                mesh_key,
                "mesh",
                term.preferred_label,
                {"descriptor_ui": term.descriptor_ui, "tree_numbers": term.tree_numbers},
            )
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    scope_key=scope_key,
                    source_key=f"paper:{mapping.paper_id}",
                    relation="HAS_MESH",
                    target_key=mesh_key,
                    evidence_chunk_id=None,
                    evidence_key="",
                    provenance="pubmed_mesh",
                    confidence=1.0 if mapping.is_major_topic else 0.9,
                )
            )

        mesh_by_tree = {
            tree: term.descriptor_ui
            for _mapping, term in mesh_rows
            for tree in term.tree_numbers
        }
        seen_hierarchy: set[tuple[str, str]] = set()
        for _mapping, term in mesh_rows:
            for tree in term.tree_numbers:
                if "." not in tree:
                    continue
                parent_tree = tree.rsplit(".", 1)[0]
                parent_ui = mesh_by_tree.get(parent_tree)
                if not parent_ui or parent_ui == term.descriptor_ui:
                    continue
                pair = (term.descriptor_ui, parent_ui)
                if pair in seen_hierarchy:
                    continue
                seen_hierarchy.add(pair)
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        scope_key=scope_key,
                        source_key=f"mesh:{term.descriptor_ui}",
                        relation="BROADER_THAN",
                        target_key=f"mesh:{parent_ui}",
                        evidence_chunk_id=None,
                        evidence_key="",
                        provenance="mesh_tree",
                        confidence=1.0,
                    )
                )

        tag_rows = (
            await self._session.execute(
                select(PaperTag, Tag)
                .join(Tag, Tag.id == PaperTag.tag_id)
                .where(
                    PaperTag.project_id == project_id,
                    PaperTag.paper_id.in_(selected_paper_ids),
                )
            )
        ).all()
        for mapping, tag in tag_rows:
            tag_key = f"tag:{tag.id}"
            add_node(
                tag_key,
                "tag",
                tag.name,
                {"kind": tag.kind, "normalized_name": tag.normalized_name},
            )
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    scope_key=scope_key,
                    source_key=f"paper:{mapping.paper_id}",
                    relation="HAS_TAG",
                    target_key=tag_key,
                    evidence_chunk_id=None,
                    evidence_key="",
                    provenance=f"paper_tag:{mapping.origin}",
                    confidence=0.95 if mapping.origin == "manual" else 0.8,
                )
            )

        citations = (
            await self._session.scalars(
                select(PaperCitation).where(
                    PaperCitation.source_paper_id.in_(selected_paper_ids),
                    PaperCitation.target_paper_id.in_(selected_paper_ids),
                )
            )
        ).all()
        for citation in citations:
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    scope_key=scope_key,
                    source_key=f"paper:{citation.source_paper_id}",
                    relation="CITES",
                    target_key=f"paper:{citation.target_paper_id}",
                    evidence_chunk_id=None,
                    evidence_key="",
                    provenance=citation.source_name,
                    confidence=1.0,
                )
            )

        selected_chunk_ids = {chunk.id for chunk, _paper_id in chunk_rows}
        claim_rows: list[tuple[Claim, EvidenceLink]] = []
        if selected_chunk_ids:
            claim_rows = [
                (claim, link)
                for claim, link in (
                    await self._session.execute(
                        select(Claim, EvidenceLink)
                        .join(EvidenceLink, EvidenceLink.claim_id == Claim.id)
                        .where(
                            Claim.project_id == project_id,
                            EvidenceLink.chunk_id.in_(selected_chunk_ids),
                            EvidenceLink.mechanically_validated.is_(True),
                        )
                    )
                ).all()
            ]
        for claim, link in claim_rows:
            claim_key = f"claim:{claim.id}"
            add_node(claim_key, "claim", claim.statement, {"status": claim.status.value})
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    scope_key=scope_key,
                    source_key=claim_key,
                    relation=link.relation.value.upper(),
                    target_key=f"chunk:{link.chunk_id}",
                    evidence_chunk_id=link.chunk_id,
                    evidence_key=str(link.chunk_id),
                    provenance="verified_claim",
                    confidence=link.support_score or 1.0,
                )
            )
            paper_id = next(
                (
                    value
                    for chunk, value in chunk_rows
                    if chunk.id == link.chunk_id
                ),
                None,
            )
            if paper_id is not None:
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        scope_key=scope_key,
                        source_key=claim_key,
                        relation="ABOUT_PAPER",
                        target_key=f"paper:{paper_id}",
                        evidence_chunk_id=link.chunk_id,
                        evidence_key=str(link.chunk_id),
                        provenance="verified_claim",
                        confidence=link.support_score or 1.0,
                    )
                )

        if scope_key == "project":
            facts = list(
                (
                    await self._session.scalars(
                        select(ProjectFact).where(
                            ProjectFact.project_id == project_id,
                            ProjectFact.status == "active",
                        )
                    )
                ).all()
            )
            for fact in facts:
                fact_key = f"fact:{fact.id}"
                category_key = self._entity_key("fact_category", fact.category)
                add_node(
                    category_key,
                    "fact_category",
                    fact.category,
                    {"category": fact.category},
                )
                add_node(
                    fact_key,
                    "project_fact",
                    fact.statement,
                    {
                        "category": fact.category,
                        "confidence": fact.confidence,
                        "importance": fact.importance,
                        "source_locator": fact.source_locator,
                    },
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        scope_key=scope_key,
                        source_key=fact_key,
                        relation="HAS_CATEGORY",
                        target_key=category_key,
                        evidence_chunk_id=None,
                        evidence_key="",
                        provenance="project_fact_schema",
                        confidence=1.0,
                    )
                )
                paper_value = fact.source_locator.get("paper_id")
                try:
                    paper_id = UUID(str(paper_value)) if paper_value else None
                except ValueError:
                    paper_id = None
                if paper_id in selected_paper_ids:
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            scope_key=scope_key,
                            source_key=fact_key,
                            relation="ABOUT_PAPER",
                            target_key=f"paper:{paper_id}",
                            evidence_chunk_id=None,
                            evidence_key="",
                            provenance="project_fact",
                            confidence=fact.confidence,
                        )
                    )

        self._session.add_all([*nodes.values(), *edges])
        await self._session.flush()
        graph_rag = await FullGraphRagIndexer(self._session).rebuild(
            project_id=project_id,
            scope_key=scope_key,
        )
        project.retrieval_revision += 1
        await self._session.commit()
        return GraphBuildResult(
            project_id=project_id,
            nodes=len(nodes),
            edges=len(edges) + graph_rag.mentions,
            revision=project.retrieval_revision,
            scope_key=scope_key,
            communities=graph_rag.communities,
            reports=graph_rag.reports,
            mentions=graph_rag.mentions,
        )

    @staticmethod
    def _normalize_entity(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).casefold()

    @classmethod
    def _entity_key(cls, entity_type: str, value: str) -> str:
        digest = hashlib.sha256(cls._normalize_entity(value).encode()).hexdigest()[:24]
        return f"{entity_type}:{digest}"


async def citation_neighbor_papers(
    session: AsyncSession,
    *,
    project_id: UUID,
    seed_paper_ids: set[UUID],
    limit: int,
    scope_key: str = "project",
    hops: int = 1,
) -> list[UUID]:
    if not seed_paper_ids or limit <= 0 or hops <= 0:
        return []
    visited = {f"paper:{paper_id}" for paper_id in seed_paper_ids}
    frontier = set(visited)
    neighbors: list[UUID] = []
    for _ in range(hops):
        if not frontier:
            break
        rows = (
            await session.execute(
                select(GraphEdge.source_key, GraphEdge.target_key)
                .where(
                    GraphEdge.project_id == project_id,
                    GraphEdge.scope_key == scope_key,
                    GraphEdge.relation == "CITES",
                    or_(
                        GraphEdge.source_key.in_(frontier),
                        GraphEdge.target_key.in_(frontier),
                    ),
                )
                .limit(max(limit * 8, 32))
            )
        ).all()
        next_frontier: set[str] = set()
        for source_key, target_key in rows:
            for key in (source_key, target_key):
                if key in visited or not key.startswith("paper:"):
                    continue
                visited.add(key)
                next_frontier.add(key)
                try:
                    paper_id = UUID(key.removeprefix("paper:"))
                except ValueError:
                    continue
                if paper_id not in neighbors:
                    neighbors.append(paper_id)
                if len(neighbors) >= limit:
                    return neighbors
        frontier = next_frontier
    return neighbors
