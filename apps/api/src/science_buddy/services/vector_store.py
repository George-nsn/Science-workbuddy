import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from time import perf_counter
from typing import Protocol, cast
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import ChunkEmbedding, EmbeddingModel

FLOAT32_BYTES = 4


class VectorStoreError(RuntimeError):
    """Raised when compact vectors violate their storage or model contract."""


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: UUID
    values: Sequence[float]
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class VectorMatch:
    chunk_id: UUID
    paper_id: UUID
    score: float


@dataclass(frozen=True, slots=True)
class VectorSearchMetrics:
    backend: str
    candidate_count: int
    vector_bytes: int
    fetch_ms: float
    decode_ms: float
    dot_product_ms: float
    top_k_ms: float
    estimated_working_set_bytes: int

    def as_dict(self) -> dict[str, str | int | float]:
        return {
            "backend": self.backend,
            "candidate_count": self.candidate_count,
            "vector_bytes": self.vector_bytes,
            "fetch_ms": self.fetch_ms,
            "decode_ms": self.decode_ms,
            "dot_product_ms": self.dot_product_ms,
            "top_k_ms": self.top_k_ms,
            "estimated_working_set_bytes": self.estimated_working_set_bytes,
        }


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    matches: tuple[VectorMatch, ...]
    metrics: VectorSearchMetrics


class VectorStore(Protocol):
    backend_name: str

    def add_batch(
        self,
        session: AsyncSession,
        *,
        model: EmbeddingModel,
        records: Sequence[VectorRecord],
    ) -> int: ...

    async def candidate_count(
        self,
        session: AsyncSession,
        *,
        project_id: UUID,
        collection_id: UUID | None,
        model_name: str,
        dimension: int,
    ) -> int: ...

    async def search(
        self,
        session: AsyncSession,
        *,
        query_vector: Sequence[float],
        project_id: UUID,
        collection_id: UUID | None,
        model_name: str,
        dimension: int,
        limit: int,
    ) -> VectorSearchResult: ...


def encode_float32_vector(
    values: Sequence[float],
    *,
    expected_dimension: int,
) -> bytes:
    if expected_dimension <= 0:
        raise VectorStoreError("Embedding dimension must be positive")
    if len(values) != expected_dimension:
        raise VectorStoreError(
            f"Vector dimension {len(values)} does not match model dimension "
            f"{expected_dimension}"
        )
    vector = np.asarray(values, dtype="<f4")
    if vector.ndim != 1 or not np.isfinite(vector).all():
        raise VectorStoreError("Vectors must be finite one-dimensional float values")
    return vector.tobytes(order="C")


def decode_float32_vector(blob: bytes | memoryview, *, expected_dimension: int) -> np.ndarray:
    view = memoryview(blob)
    expected_bytes = expected_dimension * FLOAT32_BYTES
    if view.nbytes != expected_bytes:
        raise VectorStoreError(
            f"Stored vector uses {view.nbytes} bytes; expected {expected_bytes} bytes "
            f"for dimension {expected_dimension}"
        )
    return np.frombuffer(view, dtype="<f4", count=expected_dimension)


class SQLiteNumpyVectorStore:
    """Exact SQLite-backed float32 search with one contiguous NumPy dot product."""

    backend_name = "sqlite-numpy-float32-v1"

    def add_batch(
        self,
        session: AsyncSession,
        *,
        model: EmbeddingModel,
        records: Sequence[VectorRecord],
    ) -> int:
        if not model.active:
            raise VectorStoreError(f"Embedding model '{model.name}' is not active")
        embeddings = [
            ChunkEmbedding(
                chunk_id=record.chunk_id,
                model_id=model.id,
                vector=encode_float32_vector(
                    record.values,
                    expected_dimension=model.dimension,
                ),
                content_hash=record.content_hash,
            )
            for record in records
        ]
        session.add_all(embeddings)
        return len(embeddings)

    async def candidate_count(
        self,
        session: AsyncSession,
        *,
        project_id: UUID,
        collection_id: UUID | None,
        model_name: str,
        dimension: int,
    ) -> int:
        collection_join = ""
        collection_filter = ""
        parameters: dict[str, object] = {
            "project_id": project_id.hex,
            "model_name": model_name,
            "dimension": dimension,
        }
        if collection_id is not None:
            collection_join = "JOIN collection_papers cp ON cp.paper_id = da.paper_id "
            collection_filter = "AND cp.collection_id = :collection_id "
            parameters["collection_id"] = collection_id.hex
        statement = text(
            "SELECT count(*) FROM chunk_embeddings ce "
            "JOIN embedding_models em ON em.id = ce.model_id "
            "JOIN chunks c ON c.id = ce.chunk_id "
            "JOIN sections s ON s.id = c.section_id "
            "JOIN document_assets da ON da.id = s.asset_id "
            "JOIN project_papers pp ON pp.paper_id = da.paper_id "
            f"{collection_join}"
            "WHERE pp.project_id = :project_id "
            "AND em.name = :model_name AND em.dimension = :dimension "
            "AND em.active = 1 AND length(ce.vector) = em.dimension * 4 "
            f"{collection_filter}"
        )
        return int((await session.execute(statement, parameters)).scalar_one())

    async def search(
        self,
        session: AsyncSession,
        *,
        query_vector: Sequence[float],
        project_id: UUID,
        collection_id: UUID | None,
        model_name: str,
        dimension: int,
        limit: int,
    ) -> VectorSearchResult:
        query = np.frombuffer(
            encode_float32_vector(query_vector, expected_dimension=dimension),
            dtype="<f4",
        )
        collection_join = ""
        collection_filter = ""
        parameters: dict[str, object] = {
            "project_id": project_id.hex,
            "model_name": model_name,
            "dimension": dimension,
        }
        if collection_id is not None:
            collection_join = "JOIN collection_papers cp ON cp.paper_id = da.paper_id "
            collection_filter = "AND cp.collection_id = :collection_id "
            parameters["collection_id"] = collection_id.hex
        statement = text(
            "SELECT ce.chunk_id, da.paper_id, ce.vector "
            "FROM chunk_embeddings ce "
            "JOIN embedding_models em ON em.id = ce.model_id "
            "JOIN chunks c ON c.id = ce.chunk_id "
            "JOIN sections s ON s.id = c.section_id "
            "JOIN document_assets da ON da.id = s.asset_id "
            "JOIN project_papers pp ON pp.paper_id = da.paper_id "
            f"{collection_join}"
            "WHERE pp.project_id = :project_id "
            "AND em.name = :model_name AND em.dimension = :dimension "
            "AND em.active = 1 "
            "AND length(ce.vector) = em.dimension * 4 "
            f"{collection_filter}"
            "ORDER BY ce.created_at, ce.id"
        )
        fetch_started = perf_counter()
        rows = (await session.execute(statement, parameters)).all()
        fetch_ms = (perf_counter() - fetch_started) * 1000
        if not rows or limit <= 0:
            return VectorSearchResult(
                (),
                VectorSearchMetrics(
                    backend=self.backend_name,
                    candidate_count=0,
                    vector_bytes=0,
                    fetch_ms=fetch_ms,
                    decode_ms=0.0,
                    dot_product_ms=0.0,
                    top_k_ms=0.0,
                    estimated_working_set_bytes=query.nbytes,
                ),
            )

        decode_started = perf_counter()
        matrix = np.empty((len(rows), dimension), dtype="<f4")
        chunk_ids: list[UUID] = []
        paper_ids: list[UUID] = []
        vector_bytes = 0
        for index, row in enumerate(rows):
            chunk_ids.append(UUID(str(row[0])))
            paper_ids.append(UUID(str(row[1])))
            vector = decode_float32_vector(row[2], expected_dimension=dimension)
            matrix[index] = vector
            vector_bytes += vector.nbytes
        decode_ms = (perf_counter() - decode_started) * 1000

        dot_started = perf_counter()
        scores = matrix @ query
        dot_product_ms = (perf_counter() - dot_started) * 1000

        top_k_started = perf_counter()
        selected = self._top_k_indices(scores, limit)
        top_k_ms = (perf_counter() - top_k_started) * 1000
        matches = tuple(
            VectorMatch(
                chunk_id=chunk_ids[int(index)],
                paper_id=paper_ids[int(index)],
                score=float(scores[int(index)]),
            )
            for index in selected
            if math.isfinite(float(scores[int(index)]))
        )
        estimated_working_set_bytes = (
            matrix.nbytes + query.nbytes + scores.nbytes + selected.nbytes
        )
        return VectorSearchResult(
            matches,
            VectorSearchMetrics(
                backend=self.backend_name,
                candidate_count=len(rows),
                vector_bytes=vector_bytes,
                fetch_ms=fetch_ms,
                decode_ms=decode_ms,
                dot_product_ms=dot_product_ms,
                top_k_ms=top_k_ms,
                estimated_working_set_bytes=estimated_working_set_bytes,
            ),
        )

    @staticmethod
    def _top_k_indices(scores: np.ndarray, limit: int) -> np.ndarray:
        count = scores.size
        selected_count = min(max(limit, 0), count)
        if selected_count == 0:
            return np.empty(0, dtype=np.int64)
        if selected_count == count:
            candidates = np.arange(count, dtype=np.int64)
        else:
            partitioned = np.argpartition(scores, count - selected_count)[
                count - selected_count :
            ]
            threshold = float(np.min(scores[partitioned]))
            above = np.flatnonzero(scores > threshold)
            equal = np.flatnonzero(scores == threshold)
            candidates = np.concatenate(
                (above, equal[: selected_count - above.size])
            ).astype(np.int64, copy=False)
        order = np.lexsort((candidates, -scores[candidates]))
        return cast(np.ndarray, candidates[order])


@lru_cache(maxsize=4)
def get_vector_store(backend: str) -> VectorStore:
    normalized = backend.strip().casefold()
    if normalized in {"numpy", "sqlite-numpy-float32-v1"}:
        return SQLiteNumpyVectorStore()
    raise ValueError(f"Unsupported vector backend: {backend}")
