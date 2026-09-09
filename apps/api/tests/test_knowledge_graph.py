from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import (
    Base,
    Chunk,
    DocumentAsset,
    GraphCommunity,
    GraphCommunityReport,
    GraphEdge,
    GraphEntityMention,
    GraphNode,
    GraphPathAudit,
    MeSHTerm,
    Paper,
    PaperCitation,
    PaperMeSH,
    Project,
    ProjectPaper,
    Section,
)
from science_buddy.services.graphrag import FullGraphRagRetriever
from science_buddy.services.knowledge_graph import (
    KnowledgeGraphBuilder,
    citation_neighbor_papers,
)


@pytest.mark.asyncio
async def test_graph_builder_persists_deterministic_citation_edges(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        project = Project(name="Graph project")
        source = Paper(title="Source paper", authors=[], publication_types=[])
        target = Paper(title="Target paper", authors=[], publication_types=[])
        session.add_all([project, source, target])
        await session.flush()
        session.add_all(
            [
                ProjectPaper(project_id=project.id, paper_id=source.id),
                ProjectPaper(project_id=project.id, paper_id=target.id),
                PaperCitation(
                    source_paper_id=source.id,
                    target_paper_id=target.id,
                    source_name="europe_pmc",
                ),
            ]
        )
        await session.commit()
        result = await KnowledgeGraphBuilder(session).rebuild(project.id)
        edges = list((await session.scalars(select(GraphEdge))).all())

    assert result.nodes == 2
    assert result.edges == 1
    assert edges[0].relation == "CITES"
    assert edges[0].provenance == "europe_pmc"
    await engine.dispose()


@pytest.mark.asyncio
async def test_citation_traversal_is_bounded_by_hop_count(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'citation-hops.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        project = Project(name="Citation traversal")
        papers = [
            Paper(title=f"Paper {index}", authors=[], publication_types=[])
            for index in range(4)
        ]
        session.add_all([project, *papers])
        await session.flush()
        session.add_all(
            [ProjectPaper(project_id=project.id, paper_id=paper.id) for paper in papers]
        )
        session.add_all(
            [
                GraphEdge(
                    project_id=project.id,
                    scope_key="project",
                    source_key=f"paper:{papers[index].id}",
                    relation="CITES",
                    target_key=f"paper:{papers[index + 1].id}",
                    evidence_key="",
                    provenance="test",
                    confidence=1.0,
                )
                for index in range(3)
            ]
        )
        await session.commit()

        one_hop = await citation_neighbor_papers(
            session,
            project_id=project.id,
            seed_paper_ids={papers[0].id},
            limit=10,
            hops=1,
        )
        two_hops = await citation_neighbor_papers(
            session,
            project_id=project.id,
            seed_paper_ids={papers[0].id},
            limit=10,
            hops=2,
        )

    assert one_hop == [papers[1].id]
    assert set(two_hops) == {papers[1].id, papers[2].id}
    assert papers[3].id not in two_hops
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_graphrag_builds_entities_communities_reports_and_query_modes(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'full-graphrag.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        project = Project(name="Full GraphRAG")
        source = Paper(
            title="BRAF signaling in thyroid neoplasms",
            journal="Graph Medicine",
            publication_year=2024,
            authors=[{"full_name": "Ada Researcher"}],
            publication_types=["Journal Article"],
        )
        target = Paper(
            title="MAPK pathway validation",
            journal="Graph Medicine",
            publication_year=2025,
            authors=[{"full_name": "Ada Researcher"}],
            publication_types=["Journal Article"],
        )
        session.add_all([project, source, target])
        await session.flush()
        session.add_all(
            [
                ProjectPaper(project_id=project.id, paper_id=source.id),
                ProjectPaper(project_id=project.id, paper_id=target.id),
                PaperCitation(
                    source_paper_id=source.id,
                    target_paper_id=target.id,
                    source_name="crossref",
                ),
            ]
        )
        mesh = MeSHTerm(
            descriptor_ui="D013964",
            preferred_label="Thyroid Neoplasms",
            tree_numbers=["C04.588.322.894"],
        )
        session.add(mesh)
        await session.flush()
        session.add_all(
            [
                PaperMeSH(paper_id=source.id, descriptor_ui=mesh.descriptor_ui),
                PaperMeSH(paper_id=target.id, descriptor_ui=mesh.descriptor_ui),
            ]
        )
        chunk_ids = []
        for index, paper in enumerate((source, target)):
            asset = DocumentAsset(
                paper_id=paper.id,
                source=AssetSource.PUBMED,
                evidence_depth=EvidenceDepth.ABSTRACT,
                parse_status=ParseStatus.READY,
                content_hash=f"asset-{index}",
                media_type="text/plain",
            )
            session.add(asset)
            await session.flush()
            section = Section(
                asset_id=asset.id,
                section_path="Abstract",
                title="Abstract",
                ordinal=0,
            )
            session.add(section)
            await session.flush()
            text = (
                "Thyroid Neoplasms involve BRAF and MAPK pathway evidence. "
                f"Study {index}."
            )
            chunk = Chunk(
                section_id=section.id,
                ordinal=0,
                text=text,
                content_hash=f"chunk-{index}",
                char_start=0,
                char_end=len(text),
                source_locator={"section_path": "Abstract"},
            )
            session.add(chunk)
            await session.flush()
            chunk_ids.append(chunk.id)
        await session.commit()

        result = await KnowledgeGraphBuilder(session).rebuild(project.id)
        nodes = list((await session.scalars(select(GraphNode))).all())
        communities = list((await session.scalars(select(GraphCommunity))).all())
        reports = list((await session.scalars(select(GraphCommunityReport))).all())
        mentions = list((await session.scalars(select(GraphEntityMention))).all())
        retriever = FullGraphRagRetriever(session)
        local = await retriever.search(
            mode="local",
            query="BRAF Thyroid Neoplasms",
            project_id=project.id,
            scope_key="project",
            seed_paper_ids={source.id},
            limit=5,
            hops=3,
        )
        global_result = await retriever.search(
            mode="global",
            query="Thyroid Neoplasms MAPK",
            project_id=project.id,
            scope_key="project",
            limit=5,
        )
        drift = await retriever.search(
            mode="drift",
            query="BRAF MAPK",
            project_id=project.id,
            scope_key="project",
            limit=5,
            hops=3,
        )
        path = await retriever.search(
            mode="path",
            query="BRAF Thyroid Neoplasms MAPK pathway",
            project_id=project.id,
            scope_key="project",
            seed_paper_ids={source.id, target.id},
            limit=5,
            hops=4,
            persist_paths=True,
        )
        audits = list((await session.scalars(select(GraphPathAudit))).all())
        first_community_ids = {value.id for value in communities}
        await KnowledgeGraphBuilder(session).rebuild(project.id)
        rebuilt_community_ids = set(
            (await session.scalars(select(GraphCommunity))).all()
        )

    assert result.communities >= 2
    assert result.reports == result.communities
    assert result.mentions >= 2
    assert {node.node_type for node in nodes} >= {
        "paper",
        "chunk",
        "mesh",
        "author",
        "journal",
    }
    assert {community.level for community in communities} == {0, 1}
    assert all(report.generated_by == "deterministic-community-template-v1" for report in reports)
    assert {mention.chunk_id for mention in mentions} == set(chunk_ids)
    assert local.hits
    assert global_result.hits
    assert drift.hits
    assert path.paths
    assert audits
    assert all("path_edges" in value for value in path.paths)
    assert first_community_ids == {value.id for value in rebuilt_community_ids}
    await engine.dispose()
