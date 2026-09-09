from collections.abc import Sequence
from uuid import UUID

from science_buddy.domain.contracts import (
    ChunkDraft,
    ChunkingService,
    ClaimDraft,
    EmbeddingService,
    EvidenceVerifier,
    HybridRetriever,
    RetrievalCandidate,
    SourceSegment,
)


class ServiceNotImplementedError(NotImplementedError):
    """Raised when a baseline contract has no production adapter yet."""


class PlaceholderChunkingService(ChunkingService):
    async def chunk(self, segments: Sequence[SourceSegment]) -> list[ChunkDraft]:
        raise ServiceNotImplementedError("Structure-aware chunking is not implemented yet")


class PlaceholderEmbeddingService(EmbeddingService):
    model_name = "intfloat/multilingual-e5-base"
    dimension = 768

    async def embed_queries(self, values: Sequence[str]) -> list[list[float]]:
        raise ServiceNotImplementedError(
            "Embedding execution is not implemented; queries must use the 'query: ' prefix"
        )

    async def embed_passages(self, values: Sequence[str]) -> list[list[float]]:
        raise ServiceNotImplementedError(
            "Embedding execution is not implemented; passages must use the 'passage: ' prefix"
        )


class PlaceholderHybridRetriever(HybridRetriever):
    async def retrieve(
        self, query: str, *, project_id: UUID, limit: int = 20
    ) -> list[RetrievalCandidate]:
        raise ServiceNotImplementedError("Hybrid retrieval is not implemented yet")


class PlaceholderEvidenceVerifier(EvidenceVerifier):
    async def verify(
        self, claims: Sequence[ClaimDraft], candidates: Sequence[RetrievalCandidate]
    ) -> list[bool]:
        raise ServiceNotImplementedError("Evidence verification is not implemented yet")
