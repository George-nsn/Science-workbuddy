import asyncio
import threading
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from science_buddy.domain.contracts import RetrievalRouteTrace
from science_buddy.infrastructure.models import Base, Chunk, JournalMetric, Paper
from science_buddy.services.embeddings import SentenceTransformerEmbeddingService
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.query_planning import DeterministicQueryPlanner
from science_buddy.services.reranking import (
    CrossEncoderReranker,
    RerankDocument,
    RerankerUnavailableError,
)
from science_buddy.services.retrieval import (
    FusedChunk,
    RankedChunk,
    RetrievalConfig,
    SQLiteHybridRetriever,
)


def retrieval_config(**overrides: Any) -> RetrievalConfig:
    values: dict[str, Any] = {
        "version": "quality-test",
        "rrf_k": 60,
        "weights": {"simple": 1.0},
        "top_k_dense": 10,
        "top_k_fts": 10,
        "top_k_simple": 10,
        "top_k_metadata": 10,
        "fused_pool": 10,
        "max_chunks_per_paper": 3,
        "context_radius": 1,
        "context_max_chars": 4000,
        "route_timeout_seconds": 5.0,
        "graph_seed_papers": 0,
        "graph_neighbors": 0,
    }
    values.update(overrides)
    return RetrievalConfig(**values)


def make_retriever(
    session: AsyncSession,
    *,
    config: RetrievalConfig,
    reranker: Any = None,
) -> SQLiteHybridRetriever:
    return SQLiteHybridRetriever(
        session,
        EvidenceTokenService("a-test-secret-that-is-long-enough"),
        workflow_id=uuid4(),
        config=config,
        reranker=reranker,
    )


class ScoreByChunkReranker:
    def __init__(self, scores: dict[UUID, float]) -> None:
        self._scores = scores

    async def score(self, _query: str, documents: Any) -> dict[UUID, float]:
        return {document.chunk_id: self._scores[document.chunk_id] for document in documents}


class UnavailableReranker:
    async def score(self, _query: str, _documents: Any) -> dict[UUID, float]:
        raise RerankerUnavailableError("optional model is unavailable")


class FakeCrossEncoder:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, str]]] = []

    def predict(
        self,
        pairs: list[tuple[str, str]],
        **_kwargs: Any,
    ) -> list[float]:
        self.calls.append(pairs)
        return [0.25, 0.9]


class FakeEmbeddingEncoder:
    def encode(self, values: list[str], **_kwargs: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in values]


@pytest.mark.asyncio
async def test_local_cross_encoder_is_lazy_and_scores_query_document_pairs() -> None:
    encoder = FakeCrossEncoder()
    factory_calls: list[str] = []

    def factory(model_name: str) -> FakeCrossEncoder:
        factory_calls.append(model_name)
        return encoder

    reranker = CrossEncoderReranker(
        model_name="test-cross-encoder",
        encoder_factory=factory,
    )
    documents = [
        RerankDocument(uuid4(), "first passage"),
        RerankDocument(uuid4(), "second passage"),
    ]

    scores = await reranker.score("test query", documents)
    await reranker.score("test query", documents)

    assert factory_calls == ["test-cross-encoder"]
    assert encoder.calls[0] == [
        ("test query", "first passage"),
        ("test query", "second passage"),
    ]
    assert scores == {documents[0].chunk_id: 0.25, documents[1].chunk_id: 0.9}


@pytest.mark.asyncio
async def test_cancelled_reranker_request_reuses_inflight_model_load() -> None:
    encoder = FakeCrossEncoder()
    started = threading.Event()
    release = threading.Event()
    factory_calls = 0

    def factory(_model_name: str) -> FakeCrossEncoder:
        nonlocal factory_calls
        factory_calls += 1
        started.set()
        assert release.wait(timeout=2)
        return encoder

    reranker = CrossEncoderReranker(encoder_factory=factory)
    documents = [RerankDocument(uuid4(), "passage"), RerankDocument(uuid4(), "other")]
    first_request = asyncio.create_task(reranker.score("query", documents))
    assert await asyncio.to_thread(started.wait, 1)
    first_request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_request
    release.set()

    scores = await reranker.score("query", documents)

    assert factory_calls == 1
    assert scores[documents[1].chunk_id] == 0.9


@pytest.mark.asyncio
async def test_cancelled_embedding_request_reuses_inflight_model_load() -> None:
    encoder = FakeEmbeddingEncoder()
    started = threading.Event()
    release = threading.Event()
    factory_calls = 0

    def factory(_model_name: str) -> FakeEmbeddingEncoder:
        nonlocal factory_calls
        factory_calls += 1
        started.set()
        assert release.wait(timeout=2)
        return encoder

    service = SentenceTransformerEmbeddingService(
        model_name="test-embedding",
        dimension=3,
        encoder_factory=factory,
    )
    first_request = asyncio.create_task(service.embed_queries(["query"]))
    assert await asyncio.to_thread(started.wait, 1)
    first_request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_request
    release.set()

    vectors = await service.embed_queries(["query"])

    assert factory_calls == 1
    assert vectors == [[1.0, 0.0, 0.0]]


@pytest.mark.asyncio
async def test_dense_route_skips_encoder_when_scope_has_no_indexed_vectors(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'no-vectors.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory_calls = 0

    def factory(_model_name: str) -> FakeEmbeddingEncoder:
        nonlocal factory_calls
        factory_calls += 1
        return FakeEmbeddingEncoder()

    service = SentenceTransformerEmbeddingService(
        model_name="test-embedding",
        dimension=3,
        encoder_factory=factory,
    )
    async with sessions() as session:
        retriever = SQLiteHybridRetriever(
            session,
            EvidenceTokenService("a-test-secret-that-is-long-enough"),
            workflow_id=uuid4(),
            config=retrieval_config(),
            embedding_service=service,
        )
        batch = await retriever._vector_candidates(
            session,
            "query",
            uuid4(),
            10,
        )

    assert batch.candidates == ()
    assert batch.metrics["embedding_skipped"] == 1
    assert factory_calls == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_encoder_scores_reorder_rrf_candidates_and_add_trace(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rerank.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first = Chunk(
        id=uuid4(),
        section_id=uuid4(),
        ordinal=0,
        text="Generic thyroid carcinoma background.",
        content_hash="first",
        char_start=0,
        char_end=38,
    )
    second = Chunk(
        id=uuid4(),
        section_id=uuid4(),
        ordinal=0,
        text="BRAF V600E predicts prognosis in papillary thyroid carcinoma.",
        content_hash="second",
        char_start=0,
        char_end=60,
    )
    ranked = {
        first.id: RankedChunk(first, uuid4(), 1.0),
        second.id: RankedChunk(second, uuid4(), 0.9),
    }
    fused = [
        FusedChunk(first.id, 0.9, (RetrievalRouteTrace("simple", 1, 1.0, 0.9),)),
        FusedChunk(second.id, 0.8, (RetrievalRouteTrace("simple", 2, 0.9, 0.8),)),
    ]
    async with sessions() as session:
        retriever = make_retriever(
            session,
            config=retrieval_config(
                reranker_enabled=True,
                reranker_model="test-reranker",
                reranker_weight=1.0,
            ),
            reranker=ScoreByChunkReranker({first.id: -2.0, second.id: 5.0}),
        )
        reranked, report = await retriever._rerank_fused("BRAF prognosis", fused, ranked)

    assert [item.chunk_id for item in reranked] == [second.id, first.id]
    assert report.applied
    assert report.model == "test-reranker"
    assert report.candidates == 2
    assert reranked[0].traces[-1].route == "reranker"
    assert reranked[0].traces[-1].raw_score == 5.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_unavailable_reranker_preserves_rrf_order(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rerank-fallback.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    chunk = Chunk(
        id=uuid4(),
        section_id=uuid4(),
        ordinal=0,
        text="Evidence text",
        content_hash="fallback",
        char_start=0,
        char_end=13,
    )
    fused = [FusedChunk(chunk.id, 0.5, ())]
    async with sessions() as session:
        retriever = make_retriever(
            session,
            config=retrieval_config(
                reranker_enabled=True,
                reranker_model="missing-reranker",
            ),
            reranker=UnavailableReranker(),
        )
        output, report = await retriever._rerank_fused(
            "query",
            fused,
            {chunk.id: RankedChunk(chunk, uuid4(), 1.0)},
        )

    assert output == fused
    assert report.enabled
    assert not report.applied
    assert report.error == "optional model is unavailable"
    await engine.dispose()


@pytest.mark.asyncio
async def test_journal_importance_is_bounded_secondary_prior(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'journal-prior.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        metric = JournalMetric(
            openalex_source_id="S1",
            display_name="High impact journal",
            metric_source="openalex",
            importance_score=1.0,
            metric_note="Open metric",
        )
        session.add(metric)
        await session.flush()
        high = Paper(
            title="High journal",
            authors=[],
            publication_types=[],
            journal_metric_id=metric.id,
        )
        low = Paper(title="Low journal", authors=[], publication_types=[])
        session.add_all([high, low])
        await session.commit()
        high_chunk = Chunk(
            id=uuid4(), section_id=uuid4(), ordinal=0, text="High", content_hash="h",
            char_start=0, char_end=4,
        )
        low_chunk = Chunk(
            id=uuid4(), section_id=uuid4(), ordinal=0, text="Low", content_hash="l",
            char_start=0, char_end=3,
        )
        fused = [FusedChunk(low_chunk.id, 1.0, ()), FusedChunk(high_chunk.id, 0.95, ())]
        ranked = {
            low_chunk.id: RankedChunk(low_chunk, low.id, 1.0),
            high_chunk.id: RankedChunk(high_chunk, high.id, 0.95),
        }
        retriever = make_retriever(
            session,
            config=retrieval_config(journal_prior_weight=0.1),
        )

        output = await retriever._apply_journal_prior(fused, ranked)

    assert [item.chunk_id for item in output] == [high_chunk.id, low_chunk.id]
    assert output[0].score == pytest.approx(0.955)
    assert output[1].score == pytest.approx(0.9)
    await engine.dispose()


@pytest.mark.asyncio
async def test_query_aware_context_keeps_only_relevant_neighbor(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'neighbors.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    paper_id = uuid4()
    anchor = Chunk(
        id=uuid4(),
        section_id=uuid4(),
        ordinal=5,
        text="BRAF V600E prognosis result.",
        content_hash="anchor",
        char_start=0,
        char_end=28,
        source_locator={"section_path": "Results"},
    )
    relevant = Chunk(
        id=uuid4(),
        section_id=anchor.section_id,
        ordinal=6,
        text="BRAF prognosis remained significant after adjustment.",
        content_hash="relevant",
        char_start=29,
        char_end=80,
        source_locator={"section_path": "Results"},
    )
    irrelevant = Chunk(
        id=uuid4(),
        section_id=anchor.section_id,
        ordinal=4,
        text="Samples were stored at minus eighty degrees.",
        content_hash="irrelevant",
        char_start=0,
        char_end=43,
        source_locator={"section_path": "Methods"},
    )
    async with sessions() as session:
        retriever = make_retriever(
            session,
            config=retrieval_config(
                neighbor_query_filter=True,
                neighbor_max_per_anchor=2,
                neighbor_min_score=0.2,
            ),
        )
        output = retriever._query_aware_neighbors(
            [RankedChunk(irrelevant, paper_id, 0.0), RankedChunk(relevant, paper_id, 0.0)],
            anchor=anchor,
            plan=DeterministicQueryPlanner().plan("BRAF prognosis"),
        )

    assert [item.chunk.id for item in output] == [relevant.id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_retracted_filter_excludes_normal_queries_but_allows_identifier_lookup(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retracted.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        active = Paper(title="Active evidence", authors=[], publication_types=[])
        retracted = Paper(
            title="Retracted evidence",
            authors=[],
            publication_types=[],
            is_retracted=True,
            retraction_status="retracted",
        )
        session.add_all([active, retracted])
        await session.commit()
        active_chunk = Chunk(
            id=uuid4(),
            section_id=uuid4(),
            ordinal=0,
            text="Active result",
            content_hash="active",
            char_start=0,
            char_end=13,
        )
        retracted_chunk = Chunk(
            id=uuid4(),
            section_id=uuid4(),
            ordinal=0,
            text="Retracted result",
            content_hash="retracted",
            char_start=0,
            char_end=16,
        )
        fused = [
            FusedChunk(retracted_chunk.id, 0.9, ()),
            FusedChunk(active_chunk.id, 0.8, ()),
        ]
        ranked = {
            retracted_chunk.id: RankedChunk(retracted_chunk, retracted.id, 1.0),
            active_chunk.id: RankedChunk(active_chunk, active.id, 0.9),
        }
        retriever = make_retriever(
            session,
            config=retrieval_config(exclude_retracted=True),
        )
        filtered, excluded = await retriever._filter_retracted(
            fused,
            ranked,
            bypass=False,
        )
        identifier_output, identifier_excluded = await retriever._filter_retracted(
            fused,
            ranked,
            bypass=True,
        )

    assert [item.chunk_id for item in filtered] == [active_chunk.id]
    assert excluded == 1
    assert identifier_output == fused
    assert identifier_excluded == 0
    await engine.dispose()
