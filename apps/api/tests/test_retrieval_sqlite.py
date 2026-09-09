from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import (
    Base,
    Chunk,
    ChunkEmbedding,
    CollectionPaper,
    DocumentAsset,
    EmbeddingModel,
    MeSHTerm,
    Paper,
    PaperMeSH,
    Project,
    ProjectPaper,
    RagCollection,
    Section,
)
from science_buddy.services.embeddings import SentenceTransformerEmbeddingService
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.query_planning import DeterministicQueryPlanner
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever
from science_buddy.services.vector_store import encode_float32_vector


class FakeEncoder:
    def encode(self, values: list[str], **_kwargs: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in values]


def fake_encoder_factory(_name: str) -> FakeEncoder:
    return FakeEncoder()


@pytest.mark.asyncio
async def test_sqlite_hybrid_retrieval_explains_routes_and_neighbors(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retrieval.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table in ("chunk_fts_english", "chunk_fts_simple"):
            await connection.exec_driver_sql(
                f"CREATE VIRTUAL TABLE {table} USING fts5("
                "chunk_id UNINDEXED, text, tokenize='porter unicode61')"
            )
            await connection.exec_driver_sql(
                f"CREATE TRIGGER {table}_ai AFTER INSERT ON chunks BEGIN "
                f"INSERT INTO {table}(chunk_id, text) VALUES (new.id, new.text); END"
            )
        await connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE paper_fts USING fts5("
            "paper_id UNINDEXED, title, abstract, journal, tokenize='porter unicode61')"
        )
        await connection.exec_driver_sql(
            "CREATE TRIGGER paper_fts_ai AFTER INSERT ON papers BEGIN "
            "INSERT INTO paper_fts(paper_id, title, abstract, journal) "
            "VALUES (new.id, new.title, new.abstract, new.journal); END"
        )
        await connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE mesh_fts USING fts5("
            "descriptor_ui UNINDEXED, label, tokenize='porter unicode61')"
        )
        await connection.exec_driver_sql(
            "CREATE TRIGGER mesh_fts_ai AFTER INSERT ON mesh_terms BEGIN "
            "INSERT INTO mesh_fts(descriptor_ui, label) "
            "VALUES (new.descriptor_ui, new.preferred_label); END"
        )

    async with sessions() as session:
        project = Project(name="Retrieval project")
        paper = Paper(
            title="BRAF V600E in papillary thyroid carcinoma",
            abstract="BRAF V600E was evaluated as a prognostic biomarker.",
            journal="Traceable Medicine",
            authors=[],
            publication_types=["Journal Article"],
        )
        session.add_all([project, paper])
        await session.flush()
        session.add(ProjectPaper(project_id=project.id, paper_id=paper.id))
        asset = DocumentAsset(
            paper_id=paper.id,
            source=AssetSource.PUBMED,
            evidence_depth=EvidenceDepth.ABSTRACT,
            parse_status=ParseStatus.READY,
            content_hash="asset-hash",
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
        first = Chunk(
            section_id=section.id,
            ordinal=0,
            text="BRAF V600E was associated with prognosis in papillary thyroid carcinoma.",
            content_hash="chunk-one",
            char_start=0,
            char_end=74,
            source_locator={"section_path": "Abstract", "pmid": "123"},
        )
        second = Chunk(
            section_id=section.id,
            ordinal=1,
            text="The association varied across clinical subgroups.",
            content_hash="chunk-two",
            char_start=75,
            char_end=125,
            source_locator={"section_path": "Abstract", "pmid": "123"},
        )
        session.add_all([first, second])
        await session.flush()
        first.next_chunk_id = second.id
        second.previous_chunk_id = first.id
        mesh = MeSHTerm(
            descriptor_ui="D013964",
            preferred_label="Thyroid Neoplasms",
            tree_numbers=[],
        )
        session.add(mesh)
        await session.flush()
        session.add(PaperMeSH(paper_id=paper.id, descriptor_ui=mesh.descriptor_ui))
        collection = RagCollection(
            project_id=project.id,
            name="Selected evidence",
            vector_status="ready",
            graph_status="ready",
        )
        session.add(collection)
        await session.flush()
        session.add(CollectionPaper(collection_id=collection.id, paper_id=paper.id))
        model = EmbeddingModel(
            name="test-e5",
            revision="default",
            dimension=3,
            active=True,
        )
        session.add(model)
        await session.flush()
        session.add_all(
            [
                ChunkEmbedding(
                    chunk_id=first.id,
                    model_id=model.id,
                    vector=encode_float32_vector(
                        [1.0, 0.0, 0.0], expected_dimension=3
                    ),
                ),
                ChunkEmbedding(
                    chunk_id=second.id,
                    model_id=model.id,
                    vector=encode_float32_vector(
                        [0.9, 0.1, 0.0], expected_dimension=3
                    ),
                ),
            ]
        )
        outside_paper = Paper(
            title="BRAF prognosis outside selected collection",
            abstract="BRAF prognosis in papillary thyroid carcinoma.",
            journal="Outside Journal",
            authors=[],
            publication_types=["Journal Article"],
        )
        session.add(outside_paper)
        await session.flush()
        session.add(ProjectPaper(project_id=project.id, paper_id=outside_paper.id))
        outside_asset = DocumentAsset(
            paper_id=outside_paper.id,
            source=AssetSource.PUBMED,
            evidence_depth=EvidenceDepth.ABSTRACT,
            parse_status=ParseStatus.READY,
            content_hash="outside-asset",
            media_type="text/plain",
        )
        session.add(outside_asset)
        await session.flush()
        outside_section = Section(
            asset_id=outside_asset.id,
            section_path="Abstract",
            title="Abstract",
            ordinal=0,
        )
        session.add(outside_section)
        await session.flush()
        outside_chunk = Chunk(
            section_id=outside_section.id,
            ordinal=0,
            text="BRAF V600E strongly predicts prognosis in this outside paper.",
            content_hash="outside-chunk",
            char_start=0,
            char_end=62,
            source_locator={"section_path": "Abstract", "pmid": "outside"},
        )
        session.add(outside_chunk)
        await session.flush()
        session.add(
            ChunkEmbedding(
                chunk_id=outside_chunk.id,
                model_id=model.id,
                vector=encode_float32_vector(
                    [1.0, 0.0, 0.0], expected_dimension=3
                ),
            )
        )
        await session.commit()

        service = SentenceTransformerEmbeddingService(
            model_name="test-e5",
            dimension=3,
            encoder_factory=fake_encoder_factory,
        )
        config = RetrievalConfig(
            version="test-v2",
            rrf_k=60,
            weights={
                "fts_english": 0.9,
                "simple": 1.1,
                "metadata": 0.8,
                "mesh": 0.8,
                "dense_original": 1.0,
                "dense_translated": 0.8,
                "graph": 0.0,
            },
            top_k_dense=10,
            top_k_fts=10,
            top_k_simple=10,
            top_k_metadata=10,
            fused_pool=10,
            max_chunks_per_paper=1,
            context_radius=1,
            context_max_chars=4000,
            route_timeout_seconds=5,
            graph_seed_papers=0,
            graph_neighbors=0,
        )
        workflow_id = uuid4()
        retriever = SQLiteHybridRetriever(
            session,
            EvidenceTokenService("a-test-secret-that-is-long-enough"),
            workflow_id=workflow_id,
            config=config,
            embedding_service=service,
            session_factory=sessions,
            collection_id=collection.id,
        )
        query = "BRAF V600E 对乳头状甲状腺癌预后的影响"
        candidates = await retriever.retrieve(
            query,
            project_id=project.id,
            limit=3,
            query_plan=DeterministicQueryPlanner().plan(query),
        )

    assert retriever.vector_used
    assert {candidate.role for candidate in candidates} == {"anchor", "neighbor"}
    anchor = next(candidate for candidate in candidates if candidate.role == "anchor")
    neighbor = next(candidate for candidate in candidates if candidate.role == "neighbor")
    assert {trace.route for trace in anchor.traces} >= {
        "fts_english",
        "dense_original",
    }
    assert neighbor.anchor_chunk_id == anchor.chunk_id
    assert neighbor.evidence_id != anchor.evidence_id
    assert {candidate.paper_id for candidate in candidates} == {paper.id}
    assert retriever.last_report is not None
    dense_route = next(
        route
        for route in retriever.last_report.routes
        if route.route == "dense_original"
    )
    assert dense_route.metrics["backend"] == "sqlite-numpy-float32-v1"
    assert dense_route.metrics["candidate_count"] == 2
    assert dense_route.metrics["vector_bytes"] == 24
    await engine.dispose()


@pytest.mark.asyncio
async def test_mesh_query_expansion_grounds_chinese_queries_in_project_mesh(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mesh-expansion.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table in ("chunk_fts_english", "chunk_fts_simple"):
            await connection.exec_driver_sql(
                f"CREATE VIRTUAL TABLE {table} USING fts5("
                "chunk_id UNINDEXED, text, tokenize='porter unicode61')"
            )
            await connection.exec_driver_sql(
                f"CREATE TRIGGER {table}_ai AFTER INSERT ON chunks BEGIN "
                f"INSERT INTO {table}(chunk_id, text) VALUES (new.id, new.text); END"
            )
        await connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE paper_fts USING fts5("
            "paper_id UNINDEXED, title, abstract, journal, tokenize='porter unicode61')"
        )
        await connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE mesh_fts USING fts5("
            "descriptor_ui UNINDEXED, label, tokenize='porter unicode61')"
        )
        await connection.exec_driver_sql(
            "CREATE TRIGGER mesh_fts_ai AFTER INSERT ON mesh_terms BEGIN "
            "INSERT INTO mesh_fts(descriptor_ui, label) "
            "VALUES (new.descriptor_ui, new.preferred_label); END"
        )

    async with sessions() as session:
        project = Project(name="Mesh expansion project")
        paper = Paper(
            title="Thyroid Neoplasms prognosis review",
            abstract="Prognostic factors for thyroid neoplasms were reviewed.",
            journal="Traceable Medicine",
            authors=[],
            publication_types=["Journal Article"],
        )
        session.add_all([project, paper])
        await session.flush()
        session.add(ProjectPaper(project_id=project.id, paper_id=paper.id))
        mesh = MeSHTerm(
            descriptor_ui="D013964",
            preferred_label="Thyroid Neoplasms",
            tree_numbers=[],
        )
        session.add(mesh)
        await session.flush()
        session.add(PaperMeSH(paper_id=paper.id, descriptor_ui=mesh.descriptor_ui))
        asset = DocumentAsset(
            paper_id=paper.id,
            source=AssetSource.PUBMED,
            evidence_depth=EvidenceDepth.ABSTRACT,
            parse_status=ParseStatus.READY,
            content_hash="mesh-expansion-asset",
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
        session.add(
            Chunk(
                section_id=section.id,
                ordinal=0,
                text="Prognostic factors for thyroid neoplasms were reviewed.",
                content_hash="mesh-expansion-chunk",
                char_start=0,
                char_end=56,
                source_locator={"section_path": "Abstract", "pmid": "456"},
            )
        )
        await session.commit()

        config = RetrievalConfig(
            version="test-mesh",
            rrf_k=60,
            weights={
                "exact": 1.5,
                "simple": 1.1,
                "dense_original": 1.0,
                "fts_english": 0.9,
                "dense_translated": 0.8,
                "metadata": 0.8,
                "mesh": 0.8,
                "graph": 0.0,
            },
            top_k_dense=10,
            top_k_fts=10,
            top_k_simple=10,
            top_k_metadata=10,
            fused_pool=10,
            max_chunks_per_paper=1,
            context_radius=1,
            context_max_chars=4000,
            route_timeout_seconds=5,
            graph_seed_papers=0,
            graph_neighbors=0,
            query_mesh_expansion=True,
            query_mesh_max_terms=4,
        )
        retriever = SQLiteHybridRetriever(
            session,
            EvidenceTokenService("a-test-secret-that-is-long-enough"),
            workflow_id=uuid4(),
            config=config,
            session_factory=sessions,
        )
        await retriever.retrieve(
            "甲状腺癌的预后如何",
            project_id=project.id,
            limit=3,
        )

    assert retriever.last_report is not None
    assert "Thyroid Neoplasms" in retriever.last_report.english_query
    assert any(
        target == "Thyroid Neoplasms"
        for _, target in retriever.last_report.expansions
    )
    await engine.dispose()
