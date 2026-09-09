from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import (
    Base,
    Chunk,
    ChunkEmbedding,
    DocumentAsset,
    EmbeddingModel,
    Paper,
    Project,
    ProjectPaper,
    Section,
)
from science_buddy.services.embeddings import index_project_chunks
from science_buddy.services.vector_store import encode_float32_vector


class FakeEmbeddingService:
    model_name = "fake-e5"
    dimension = 4
    batch_size = 16

    def __init__(self) -> None:
        self.passage_calls = 0

    async def embed_passages(self, values):  # type: ignore[no-untyped-def]
        self.passage_calls += 1
        return [[float(len(value) % 3), 0.5, 0.25, 0.1] for value in values]


async def make_database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


async def add_chunk(session: object, project: Project, text: str, content_hash: str) -> Chunk:
    paper = Paper(
        title="Chunk invalidation paper",
        journal="Traceable Medicine",
        authors=[],
        publication_types=["Journal Article"],
    )
    session.add(paper)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    session.add(ProjectPaper(project_id=project.id, paper_id=paper.id))  # type: ignore[attr-defined]
    asset = DocumentAsset(
        paper_id=paper.id,
        source=AssetSource.PUBMED,
        evidence_depth=EvidenceDepth.ABSTRACT,
        parse_status=ParseStatus.READY,
        content_hash=f"asset-{content_hash}",
        media_type="text/plain",
    )
    session.add(asset)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    section = Section(
        asset_id=asset.id,
        section_path="Abstract",
        title="Abstract",
        ordinal=0,
    )
    session.add(section)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    chunk = Chunk(
        section_id=section.id,
        ordinal=0,
        text=text,
        content_hash=content_hash,
        char_start=0,
        char_end=len(text),
        source_locator={"section_path": "Abstract"},
    )
    session.add(chunk)  # type: ignore[attr-defined]
    await session.commit()  # type: ignore[attr-defined]
    return chunk


@pytest.mark.asyncio
async def test_chunk_text_change_reembeds_and_updates_vector(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "chunk-reindex.db")
    service = FakeEmbeddingService()
    async with sessions() as session:
        project = Project(name="Reindex project")
        session.add(project)
        await session.flush()
        chunk = await add_chunk(
            session, project, "BRAF V600E predicts prognosis.", "hash-one"
        )
        assert await index_project_chunks(session, service, project_id=project.id) == 1
        first_calls = service.passage_calls

        embedding = await session.scalar(
            select(ChunkEmbedding).where(ChunkEmbedding.chunk_id == chunk.id)
        )
        assert embedding is not None
        assert embedding.content_hash == "hash-one"

        # Unchanged chunks must not be re-embedded.
        assert await index_project_chunks(session, service, project_id=project.id) == 0
        assert service.passage_calls == first_calls

        # Text change (new content hash) must invalidate the stored vector.
        chunk.text = "BRAF V600E predicts poor prognosis in subgroups."
        chunk.content_hash = "hash-two"
        await session.commit()
        assert await index_project_chunks(session, service, project_id=project.id) == 1
        await session.refresh(embedding)

    assert embedding.content_hash == "hash-two"
    assert service.passage_calls == first_calls + 1
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_legacy_embedding_without_hash_is_reembedded_once(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "legacy-hash.db")
    service = FakeEmbeddingService()
    async with sessions() as session:
        project = Project(name="Legacy project")
        session.add(project)
        await session.flush()
        chunk = await add_chunk(session, project, "Legacy chunk text.", "hash-legacy")
        # Simulate a pre-0032 row: vector present, content_hash default empty.
        model = EmbeddingModel(
            name=service.model_name,
            revision="default",
            dimension=service.dimension,
            active=True,
        )
        session.add(model)
        await session.flush()
        session.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                model_id=model.id,
                vector=encode_float32_vector(
                    [0.1, 0.2, 0.3, 0.4], expected_dimension=service.dimension
                ),
            )
        )
        await session.commit()

        assert await index_project_chunks(session, service, project_id=project.id) == 1
        embedding = await session.scalar(
            select(ChunkEmbedding).where(ChunkEmbedding.chunk_id == chunk.id)
        )
        assert embedding is not None
        assert embedding.content_hash == "hash-legacy"
        # Converged: the next run finds nothing to do.
        assert await index_project_chunks(session, service, project_id=project.id) == 0
    await engine.dispose()  # type: ignore[attr-defined]
