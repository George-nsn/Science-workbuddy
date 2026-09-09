import pytest

from science_buddy.domain.contracts import SourceSegment
from science_buddy.services.chunking import StructureAwareChunkingService, estimate_tokens
from science_buddy.services.embeddings import EmbeddingUnavailableError


class _TopicEmbedder:
    model_name = "fake-e5"

    async def embed_passages(self, values: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "oncogene" in value else [0.0, 1.0]
            for value in values
        ]


class _UnavailableEmbedder:
    model_name = "missing-e5"

    async def embed_passages(self, values: list[str]) -> list[list[float]]:
        del values
        raise EmbeddingUnavailableError("model is unavailable")


@pytest.mark.asyncio
async def test_structure_aware_chunking_preserves_source_offsets() -> None:
    text = " ".join(f"Sentence {index} reports a biomedical result." for index in range(100))
    service = StructureAwareChunkingService(
        target_tokens=80,
        max_tokens=100,
        overlap_tokens=10,
        min_tokens=10,
    )

    chunks = await service.chunk(
        [
            SourceSegment(
                text=text,
                section_path="Results",
                page_start=3,
                page_end=4,
                char_start=500,
                char_end=500 + len(text),
            )
        ]
    )

    assert len(chunks) > 1
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        local_start = chunk.source.char_start - 500
        local_end = chunk.source.char_end - 500
        assert text[local_start:local_end] == chunk.text
        assert chunk.token_count <= 100
        assert chunk.source.page_start == 3


def test_token_estimate_handles_cjk_and_gene_symbols() -> None:
    assert estimate_tokens("BRAF V600E 与甲状腺癌") >= 6


@pytest.mark.asyncio
async def test_e5_semantic_drop_creates_boundary_inside_structure() -> None:
    text = " ".join(
        [
            "The oncogene drives tumor growth in cultured human cells.",
            "This oncogene also activates a measurable kinase pathway.",
            "Targeting the oncogene reduces viability in the disease model.",
            "The orbit contains a rocky planet around a distant star.",
            "This orbit remains stable throughout the telescope observation.",
            "Astronomers calculated the orbit from repeated spectral measurements.",
        ]
    )
    service = StructureAwareChunkingService(
        target_tokens=120,
        max_tokens=140,
        overlap_tokens=0,
        min_tokens=15,
        semantic_embedder=_TopicEmbedder(),
    )

    chunks = await service.chunk(
        [SourceSegment(text, "Results", 2, 2, 100, 100 + len(text))]
    )

    assert len(chunks) == 2
    assert "oncogene" in chunks[0].text
    assert "orbit" not in chunks[0].text
    assert "orbit" in chunks[1].text
    assert "oncogene" not in chunks[1].text
    for chunk in chunks:
        start = chunk.source.char_start - 100
        end = chunk.source.char_end - 100
        assert text[start:end] == chunk.text


@pytest.mark.asyncio
async def test_semantic_chunks_preserve_source_overlap() -> None:
    sentences = [
        "The oncogene drives tumor growth in cultured human cells.",
        "This oncogene also activates a measurable kinase pathway.",
        "Targeting the oncogene reduces viability in the disease model.",
        "The orbit contains a rocky planet around a distant star.",
        "This orbit remains stable throughout the telescope observation.",
        "Astronomers calculated the orbit from repeated spectral measurements.",
    ]
    text = " ".join(sentences)
    service = StructureAwareChunkingService(
        target_tokens=120,
        max_tokens=140,
        overlap_tokens=12,
        min_tokens=15,
        semantic_embedder=_TopicEmbedder(),
    )

    chunks = await service.chunk(
        [SourceSegment(text, "Results", 2, 2, 0, len(text))]
    )

    assert len(chunks) == 2
    assert chunks[1].text.startswith(sentences[2])
    assert chunks[1].source.char_start == text.index(sentences[2])
    assert chunks[1].token_count <= 140


@pytest.mark.asyncio
async def test_semantic_chunking_keeps_structure_boundaries_hard() -> None:
    first = "The oncogene drives tumor growth. " * 5
    second = "The oncogene remains detectable after treatment. " * 5
    service = StructureAwareChunkingService(
        target_tokens=120,
        max_tokens=140,
        overlap_tokens=0,
        min_tokens=10,
        semantic_embedder=_TopicEmbedder(),
    )

    chunks = await service.chunk(
        [
            SourceSegment(first, "Methods", 1, 1, 0, len(first)),
            SourceSegment(second, "Results", 2, 2, 500, 500 + len(second)),
        ]
    )

    assert [chunk.section_path for chunk in chunks] == ["Methods", "Results"]
    assert chunks[0].source.char_start == 0
    assert chunks[1].source.char_start == 500


@pytest.mark.asyncio
async def test_unavailable_e5_falls_back_to_deterministic_chunking() -> None:
    text = " ".join(f"Sentence {index} reports a result." for index in range(30))
    segment = SourceSegment(text, "Results", 1, 1, 50, 50 + len(text))
    settings = {
        "target_tokens": 40,
        "max_tokens": 50,
        "overlap_tokens": 5,
        "min_tokens": 5,
    }
    fallback = StructureAwareChunkingService(
        **settings,
        semantic_embedder=_UnavailableEmbedder(),
    )
    deterministic = StructureAwareChunkingService(**settings)

    fallback_chunks = await fallback.chunk([segment])
    deterministic_chunks = await deterministic.chunk([segment])

    assert fallback_chunks == deterministic_chunks
