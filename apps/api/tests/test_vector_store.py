from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.domain.enums import AssetSource, EvidenceDepth, ParseStatus
from science_buddy.infrastructure.models import (
    Base,
    Chunk,
    ChunkEmbedding,
    CollectionPaper,
    DocumentAsset,
    EmbeddingModel,
    Paper,
    Project,
    ProjectPaper,
    RagCollection,
    Section,
)
from science_buddy.services.embeddings import ensure_embedding_model
from science_buddy.services.vector_store import (
    SQLiteNumpyVectorStore,
    VectorRecord,
    VectorStoreError,
    decode_float32_vector,
    encode_float32_vector,
)


async def add_chunk(
    session,  # type: ignore[no-untyped-def]
    *,
    project: Project,
    paper: Paper,
    ordinal: int,
) -> Chunk:
    session.add(ProjectPaper(project_id=project.id, paper_id=paper.id))
    asset = DocumentAsset(
        paper_id=paper.id,
        source=AssetSource.USER_PDF,
        evidence_depth=EvidenceDepth.USER_PDF,
        parse_status=ParseStatus.READY,
        content_hash=f"asset-{paper.id}",
        media_type="application/pdf",
    )
    session.add(asset)
    await session.flush()
    section = Section(
        asset_id=asset.id,
        section_path="Results",
        title="Results",
        ordinal=0,
    )
    session.add(section)
    await session.flush()
    chunk = Chunk(
        section_id=section.id,
        ordinal=ordinal,
        text=f"Evidence {ordinal}",
        content_hash=f"chunk-{paper.id}",
        char_start=0,
        char_end=20,
    )
    session.add(chunk)
    await session.flush()
    return chunk


def test_float32_codec_is_compact_and_rejects_invalid_values() -> None:
    blob = encode_float32_vector([1.0, -0.5, 0.25], expected_dimension=3)

    assert len(blob) == 12
    assert decode_float32_vector(blob, expected_dimension=3).tolist() == [1.0, -0.5, 0.25]
    with pytest.raises(VectorStoreError, match="does not match"):
        encode_float32_vector([1.0], expected_dimension=3)
    with pytest.raises(VectorStoreError, match="finite"):
        encode_float32_vector([1.0, float("nan"), 0.0], expected_dimension=3)
    with pytest.raises(VectorStoreError, match="uses 4 bytes"):
        decode_float32_vector(blob[:4], expected_dimension=3)


@pytest.mark.asyncio
async def test_numpy_vector_store_filters_scope_and_matches_exact_reference(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vectors.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = SQLiteNumpyVectorStore()
    async with sessions() as session:
        project = Project(name="Vector project")
        outside_project = Project(name="Outside project")
        selected_paper = Paper(title="Selected", authors=[], publication_types=[])
        other_paper = Paper(title="Other", authors=[], publication_types=[])
        outside_paper = Paper(title="Outside", authors=[], publication_types=[])
        session.add_all(
            [project, outside_project, selected_paper, other_paper, outside_paper]
        )
        await session.flush()
        selected_chunk = await add_chunk(
            session,
            project=project,
            paper=selected_paper,
            ordinal=0,
        )
        other_chunk = await add_chunk(
            session,
            project=project,
            paper=other_paper,
            ordinal=1,
        )
        outside_chunk = await add_chunk(
            session,
            project=outside_project,
            paper=outside_paper,
            ordinal=2,
        )
        collection = RagCollection(
            project_id=project.id,
            name="Selected only",
            vector_status="ready",
            graph_status="not_requested",
        )
        model = EmbeddingModel(
            name="test-e5",
            revision="default",
            dimension=3,
            active=True,
        )
        session.add_all([collection, model])
        await session.flush()
        session.add(
            CollectionPaper(collection_id=collection.id, paper_id=selected_paper.id)
        )
        vectors = {
            selected_chunk.id: [0.8, 0.2, 0.0],
            other_chunk.id: [1.0, 0.0, 0.0],
            outside_chunk.id: [2.0, 0.0, 0.0],
        }
        store.add_batch(
            session,
            model=model,
            records=[
                VectorRecord(chunk_id=chunk_id, values=vector)
                for chunk_id, vector in vectors.items()
            ],
        )
        await session.commit()

        project_result = await store.search(
            session,
            query_vector=[1.0, 0.0, 0.0],
            project_id=project.id,
            collection_id=None,
            model_name="test-e5",
            dimension=3,
            limit=2,
        )
        collection_result = await store.search(
            session,
            query_vector=[1.0, 0.0, 0.0],
            project_id=project.id,
            collection_id=collection.id,
            model_name="test-e5",
            dimension=3,
            limit=5,
        )
        wrong_model = await store.search(
            session,
            query_vector=[1.0, 0.0, 0.0],
            project_id=project.id,
            collection_id=None,
            model_name="other-model",
            dimension=3,
            limit=5,
        )

    expected = sorted(
        (selected_chunk.id, other_chunk.id),
        key=lambda chunk_id: float(
            np.dot(
                np.asarray(vectors[chunk_id], dtype=np.float32),
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            )
        ),
        reverse=True,
    )
    assert [match.chunk_id for match in project_result.matches] == expected
    assert [match.chunk_id for match in collection_result.matches] == [selected_chunk.id]
    assert wrong_model.matches == ()
    assert project_result.metrics.backend == "sqlite-numpy-float32-v1"
    assert project_result.metrics.candidate_count == 2
    assert project_result.metrics.vector_bytes == 24
    assert project_result.metrics.estimated_working_set_bytes > 0
    assert outside_chunk.id not in {match.chunk_id for match in project_result.matches}
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_and_model_validation_reject_wrong_dimensions(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vector-validation.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        project = Project(name="Validation project")
        paper = Paper(title="Validation paper", authors=[], publication_types=[])
        session.add_all([project, paper])
        await session.flush()
        chunk = await add_chunk(session, project=project, paper=paper, ordinal=0)
        model = await ensure_embedding_model(session, name="validated-model", dimension=3)
        await session.commit()
        chunk_id = chunk.id

        session.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                model_id=model.id,
                vector=encode_float32_vector([1.0, 0.0], expected_dimension=2),
            )
        )
        with pytest.raises(IntegrityError, match="compact vector dimension"):
            await session.commit()
        await session.rollback()

        with pytest.raises(ValueError, match="registered with dimension 3"):
            await ensure_embedding_model(session, name="validated-model", dimension=2)

        inactive = EmbeddingModel(
            name="inactive-model",
            revision="default",
            dimension=3,
            active=False,
        )
        session.add(inactive)
        await session.flush()
        with pytest.raises(VectorStoreError, match="not active"):
            SQLiteNumpyVectorStore().add_batch(
                session,
                model=inactive,
                    records=[VectorRecord(chunk_id, [1.0, 0.0, 0.0])],
            )

    await engine.dispose()
