import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from science_buddy.domain.contracts import (
    ChunkDraft,
    EmbeddingUnavailableError,
    PassageEmbeddingService,
    SourceSegment,
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_./][A-Za-z0-9]+)*|[\u3400-\u9fff]|[^\s]")
_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?。！？])(?:[\"'’”）)\]]*)\s+|\n{2,}")

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TextUnit:
    text: str
    start: int
    end: int
    tokens: int


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate for English and CJK text."""
    return len(_TOKEN_PATTERN.findall(text))


def _trimmed_unit(text: str, start: int, end: int) -> _TextUnit | None:
    raw = text[start:end]
    left_trimmed = len(raw) - len(raw.lstrip())
    right_value = raw.rstrip()
    if not right_value:
        return None
    unit_start = start + left_trimmed
    unit_end = start + len(right_value)
    value = text[unit_start:unit_end]
    return _TextUnit(value, unit_start, unit_end, estimate_tokens(value))


def _split_sentences(text: str) -> list[_TextUnit]:
    units: list[_TextUnit] = []
    cursor = 0
    for boundary in _BOUNDARY_PATTERN.finditer(text):
        unit = _trimmed_unit(text, cursor, boundary.start())
        if unit:
            units.append(unit)
        cursor = boundary.end()
    final = _trimmed_unit(text, cursor, len(text))
    if final:
        units.append(final)
    return units


def _split_oversized(unit: _TextUnit, max_tokens: int) -> list[_TextUnit]:
    if unit.tokens <= max_tokens:
        return [unit]
    matches = list(_TOKEN_PATTERN.finditer(unit.text))
    pieces: list[_TextUnit] = []
    for offset in range(0, len(matches), max_tokens):
        token_group = matches[offset : offset + max_tokens]
        local_start = token_group[0].start()
        local_end = token_group[-1].end()
        value = unit.text[local_start:local_end]
        pieces.append(
            _TextUnit(
                text=value,
                start=unit.start + local_start,
                end=unit.start + local_end,
                tokens=len(token_group),
            )
        )
    return pieces


class StructureAwareChunkingService:
    """Chunk structured segments with optional local semantic boundary detection."""

    def __init__(
        self,
        *,
        target_tokens: int = 384,
        max_tokens: int = 480,
        overlap_tokens: int = 64,
        min_tokens: int = 80,
        semantic_embedder: PassageEmbeddingService | None = None,
        semantic_min_drop: float = 0.08,
        semantic_mad_scale: float = 2.5,
        semantic_similarity_floor: float = 0.55,
    ) -> None:
        if not 0 <= overlap_tokens < target_tokens <= max_tokens:
            raise ValueError("Expected 0 <= overlap < target <= max")
        if not 0 < min_tokens <= target_tokens:
            raise ValueError("min_tokens must be between zero and target_tokens")
        if not 0 < semantic_min_drop <= 2:
            raise ValueError("semantic_min_drop must be between zero and two")
        if semantic_mad_scale < 0:
            raise ValueError("semantic_mad_scale cannot be negative")
        if not -1 <= semantic_similarity_floor <= 1:
            raise ValueError("semantic_similarity_floor must be between minus one and one")
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.min_tokens = min_tokens
        self.semantic_embedder = semantic_embedder
        self.semantic_min_drop = semantic_min_drop
        self.semantic_mad_scale = semantic_mad_scale
        self.semantic_similarity_floor = semantic_similarity_floor
        self._fallback_logged = False

    async def chunk(self, segments: Sequence[SourceSegment]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        ordinal = 0
        for segment in segments:
            units = [
                piece
                for sentence in _split_sentences(segment.text)
                for piece in _split_oversized(sentence, self.max_tokens)
            ]
            semantic_boundaries = await self._semantic_boundary_indexes(units)
            segment_chunks = self._aggregate(
                segment,
                units,
                semantic_boundaries=semantic_boundaries,
            )
            for source, token_count in segment_chunks:
                drafts.append(
                    ChunkDraft(
                        text=source.text,
                        section_path=segment.section_path,
                        ordinal=ordinal,
                        token_count=token_count,
                        source=source,
                    )
                )
                ordinal += 1
        return drafts

    async def _semantic_boundary_indexes(self, units: Sequence[_TextUnit]) -> set[int]:
        if self.semantic_embedder is None or len(units) < 2:
            return set()
        try:
            vectors = await self.semantic_embedder.embed_passages([unit.text for unit in units])
        except EmbeddingUnavailableError as exc:
            if not self._fallback_logged:
                logger.warning(
                    "semantic_chunking_fallback model=%s reason=%s",
                    self.semantic_embedder.model_name,
                    exc,
                )
                self._fallback_logged = True
            return set()
        if len(vectors) != len(units):
            raise ValueError("Semantic embedder returned an unexpected vector count")
        similarities = [
            _cosine_similarity(left, right)
            for left, right in zip(vectors, vectors[1:], strict=False)
        ]
        center = median(similarities)
        mad = median(abs(value - center) for value in similarities)
        adaptive_threshold = center - max(
            self.semantic_min_drop,
            self.semantic_mad_scale * mad,
        )
        return {
            index + 1
            for index, similarity in enumerate(similarities)
            if similarity <= self.semantic_similarity_floor
            or similarity <= adaptive_threshold
        }

    def _aggregate(
        self,
        segment: SourceSegment,
        units: Sequence[_TextUnit],
        *,
        semantic_boundaries: set[int] | None = None,
    ) -> list[tuple[SourceSegment, int]]:
        if not units:
            return []
        boundaries = semantic_boundaries or set()
        groups: list[list[_TextUnit]] = []
        current: list[_TextUnit] = []
        current_tokens = 0
        for index, unit in enumerate(units):
            semantic_break = index in boundaries and current_tokens >= self.min_tokens
            length_break = bool(
                current and current_tokens + unit.tokens > self.target_tokens
            )
            if current and (semantic_break or length_break):
                groups.append(current)
                current = []
                current_tokens = 0
            current.append(unit)
            current_tokens += unit.tokens
        if current:
            groups.append(current)

        if len(groups) > 1 and sum(unit.tokens for unit in groups[-1]) < self.min_tokens:
            previous_group = groups[-2]
            tail = groups[-1]
            combined_tokens = sum(unit.tokens for unit in previous_group + tail)
            if combined_tokens <= self.max_tokens:
                groups[-2] = previous_group + [
                    unit for unit in tail if unit not in previous_group
                ]
                groups.pop()

        results: list[tuple[SourceSegment, int]] = []
        previous_core: list[_TextUnit] = []
        for core_group in groups:
            overlap: list[_TextUnit] = []
            overlap_count = 0
            for previous in reversed(previous_core):
                if overlap_count + previous.tokens > self.overlap_tokens:
                    break
                overlap.insert(0, previous)
                overlap_count += previous.tokens
            core_tokens = sum(unit.tokens for unit in core_group)
            while overlap and overlap_count + core_tokens > self.max_tokens:
                overlap_count -= overlap.pop(0).tokens
            group = [*overlap, *core_group]
            start = group[0].start
            end = core_group[-1].end
            text = segment.text[start:end]
            source = SourceSegment(
                text=text,
                section_path=segment.section_path,
                page_start=segment.page_start,
                page_end=segment.page_end,
                char_start=segment.char_start + start,
                char_end=segment.char_start + end,
            )
            results.append((source, estimate_tokens(text)))
            previous_core = core_group
        return results


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Semantic embedding vectors must have equal non-zero dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("Semantic embedding vectors cannot be zero vectors")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def recommended_chunk_count(text: str, target_tokens: int = 384) -> int:
    """Expose a stable estimate for progress reporting without parsing a document."""
    return max(1, math.ceil(estimate_tokens(text) / target_tokens))
