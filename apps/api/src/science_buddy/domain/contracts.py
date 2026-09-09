from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class EmbeddingUnavailableError(RuntimeError):
    """Raised when a local embedding runtime or model cannot be used."""


class PassageEmbeddingService(Protocol):
    model_name: str

    async def embed_passages(self, values: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class SourceSegment:
    text: str
    section_path: str
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    text: str
    section_path: str
    ordinal: int
    token_count: int
    source: SourceSegment


@dataclass(frozen=True, slots=True)
class RetrievalRouteTrace:
    route: str
    rank: int
    raw_score: float
    weighted_rrf: float


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: UUID
    evidence_id: str
    text: str
    score: float
    source_locator: dict[str, object]
    paper_id: UUID | None = None
    content_hash: str | None = None
    traces: tuple[RetrievalRouteTrace, ...] = ()
    role: str = "anchor"
    anchor_chunk_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ClaimDraft:
    statement: str
    evidence_ids: tuple[str, ...]


class ChunkingService(Protocol):
    async def chunk(self, segments: Sequence[SourceSegment]) -> list[ChunkDraft]: ...


class EmbeddingService(Protocol):
    model_name: str
    dimension: int

    async def embed_queries(self, values: Sequence[str]) -> list[list[float]]: ...

    async def embed_passages(self, values: Sequence[str]) -> list[list[float]]: ...


class HybridRetriever(Protocol):
    async def retrieve(
        self, query: str, *, project_id: UUID, limit: int = 20
    ) -> list[RetrievalCandidate]: ...


class EvidenceVerifier(Protocol):
    async def verify(
        self, claims: Sequence[ClaimDraft], candidates: Sequence[RetrievalCandidate]
    ) -> list[bool]: ...
