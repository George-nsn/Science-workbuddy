import asyncio
import importlib
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.config import get_settings
from science_buddy.domain.contracts import EmbeddingUnavailableError
from science_buddy.infrastructure.models import (
    Chunk,
    ChunkEmbedding,
    CollectionPaper,
    DocumentAsset,
    EmbeddingModel,
    Project,
    ProjectPaper,
    RagCollection,
    Section,
)
from science_buddy.services.vector_store import (
    VectorRecord,
    VectorStore,
    encode_float32_vector,
    get_vector_store,
)

__all__ = ["EmbeddingUnavailableError", "SentenceTransformerEmbeddingService"]


class SentenceTransformerEmbeddingService:
    """Local E5 embedding adapter with mandatory asymmetric prefixes."""

    def __init__(
        self,
        *,
        model_name: str = "intfloat/multilingual-e5-base",
        dimension: int = 768,
        batch_size: int = 16,
        encoder_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self.batch_size = batch_size
        self._encoder_factory = encoder_factory
        self._encoder: Any | None = None
        self._load_error: EmbeddingUnavailableError | None = None
        self._load_task: asyncio.Task[Any] | None = None
        self._load_lock = asyncio.Lock()
        self._encode_lock = asyncio.Lock()

    def _load_encoder_sync(self) -> Any:
        factory = self._encoder_factory
        if factory is None:
            module = importlib.import_module("sentence_transformers")
            factory = module.SentenceTransformer
        return factory(self.model_name)

    async def _load_encoder(self) -> Any:
        try:
            self._encoder = await asyncio.to_thread(self._load_encoder_sync)
        except ImportError as exc:
            error = EmbeddingUnavailableError(
                "Install the 'rag' optional dependency to run local embeddings"
            )
            self._load_error = error
            raise error from exc
        except (OSError, RuntimeError) as exc:
            error = EmbeddingUnavailableError(
                f"Local embedding model '{self.model_name}' could not be loaded"
            )
            self._load_error = error
            raise error from exc
        return self._encoder

    async def _get_encoder(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        if self._load_error is not None:
            raise self._load_error
        async with self._load_lock:
            if self._encoder is not None:
                return self._encoder
            if self._load_error is not None:
                raise self._load_error
            if self._load_task is None:
                self._load_task = asyncio.create_task(self._load_encoder())
            load_task = self._load_task
        return await asyncio.shield(load_task)

    async def embed_queries(self, values: Sequence[str]) -> list[list[float]]:
        return await self._encode([f"query: {value.strip()}" for value in values])

    async def embed_passages(self, values: Sequence[str]) -> list[list[float]]:
        return await self._encode([f"passage: {value.strip()}" for value in values])

    async def _encode(self, values: Sequence[str]) -> list[list[float]]:
        if not values:
            return []
        encoder = await self._get_encoder()
        async with self._encode_lock:
            try:
                vectors: Any = await asyncio.to_thread(
                    encoder.encode,
                    list(values),
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
            except (OSError, RuntimeError) as exc:
                raise EmbeddingUnavailableError(
                    f"Local embedding model '{self.model_name}' failed during inference"
                ) from exc
        result = vectors.tolist() if hasattr(vectors, "tolist") else vectors
        output = [[float(component) for component in vector] for vector in result]
        if any(len(vector) != self.dimension for vector in output):
            raise ValueError(
                f"Embedding model returned a dimension other than {self.dimension}"
            )
        return output


@lru_cache(maxsize=1)
def get_embedding_service() -> SentenceTransformerEmbeddingService:
    """Return one lazy encoder per API process; the worker owns its own process-local copy."""
    settings = get_settings()
    return SentenceTransformerEmbeddingService(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
        batch_size=16,
    )


async def ensure_embedding_model(
    session: AsyncSession,
    *,
    name: str,
    dimension: int,
) -> EmbeddingModel:
    model = await session.scalar(
        select(EmbeddingModel).where(
            EmbeddingModel.name == name,
            EmbeddingModel.revision == "default",
        )
    )
    if model is None:
        model = EmbeddingModel(
            name=name,
            revision="default",
            dimension=dimension,
            query_prefix="query: ",
            passage_prefix="passage: ",
            active=True,
        )
        session.add(model)
        await session.flush()
    elif model.dimension != dimension:
        raise ValueError(
            f"Embedding model '{name}' revision 'default' is registered with dimension "
            f"{model.dimension}, not {dimension}"
        )
    elif not model.active:
        model.active = True
        await session.flush()
    return model


async def index_project_chunks(
    session: AsyncSession,
    service: SentenceTransformerEmbeddingService,
    *,
    project_id: UUID,
    paper_ids: set[UUID] | None = None,
    vector_store: VectorStore | None = None,
) -> int:
    model = await ensure_embedding_model(
        session,
        name=service.model_name,
        dimension=service.dimension,
    )
    statement = (
        select(Chunk, ChunkEmbedding)
        .join(Section, Chunk.section_id == Section.id)
        .join(DocumentAsset, Section.asset_id == DocumentAsset.id)
        .join(ProjectPaper, ProjectPaper.paper_id == DocumentAsset.paper_id)
        .outerjoin(
            ChunkEmbedding,
            (ChunkEmbedding.chunk_id == Chunk.id) & (ChunkEmbedding.model_id == model.id),
        )
        .where(
            ProjectPaper.project_id == project_id,
            or_(
                ChunkEmbedding.id.is_(None),
                ChunkEmbedding.content_hash != Chunk.content_hash,
            ),
        )
        .order_by(Chunk.created_at)
    )
    if paper_ids is not None:
        statement = statement.where(DocumentAsset.paper_id.in_(paper_ids))
    rows = list((await session.execute(statement)).all())
    store = vector_store or get_vector_store(get_settings().retrieval_vector_backend)
    indexed = 0
    for offset in range(0, len(rows), service.batch_size):
        batch = rows[offset : offset + service.batch_size]
        chunks = [chunk for chunk, _ in batch]
        vectors = await service.embed_passages([chunk.text for chunk in chunks])
        new_records: list[VectorRecord] = []
        for (chunk, embedding), vector in zip(batch, vectors, strict=True):
            if embedding is None:
                new_records.append(
                    VectorRecord(
                        chunk_id=chunk.id,
                        values=vector,
                        content_hash=chunk.content_hash,
                    )
                )
                continue
            embedding.vector = encode_float32_vector(
                vector,
                expected_dimension=model.dimension,
            )
            embedding.content_hash = chunk.content_hash
        if new_records:
            store.add_batch(
                session,
                model=model,
                records=new_records,
            )
        await session.commit()
        indexed += len(batch)
    if indexed:
        project = await session.get(Project, project_id)
        if project is not None:
            project.retrieval_revision += 1
            await session.commit()
    return indexed


async def index_collection_chunks(
    session: AsyncSession,
    service: SentenceTransformerEmbeddingService,
    *,
    collection_id: UUID,
    vector_store: VectorStore | None = None,
) -> int:
    collection = await session.get(RagCollection, collection_id)
    if collection is None:
        raise ValueError("RAG collection does not exist")
    paper_ids = set(
        (
            await session.scalars(
                select(CollectionPaper.paper_id).where(
                    CollectionPaper.collection_id == collection_id
                )
            )
        ).all()
    )
    collection.vector_status = "building"
    await session.commit()
    try:
        indexed = await index_project_chunks(
            session,
            service,
            project_id=collection.project_id,
            paper_ids=paper_ids,
            vector_store=vector_store,
        )
        collection = await session.get(RagCollection, collection_id)
        if collection is not None:
            collection.vector_status = "ready"
            await session.commit()
        return indexed
    except Exception:
        await session.rollback()
        collection = await session.get(RagCollection, collection_id)
        if collection is not None:
            collection.vector_status = "failed"
            await session.commit()
        raise
