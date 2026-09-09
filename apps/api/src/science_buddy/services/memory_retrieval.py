import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import numpy as np
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    MemoryDerivedIndex,
    ProjectFact,
    ProjectFactRelation,
    ResearchClaimTarget,
    ResearchRun,
    ResearchStepMemory,
    RetrievalRouteMemory,
)
from science_buddy.services.embeddings import SentenceTransformerEmbeddingService
from science_buddy.services.vector_store import decode_float32_vector, encode_float32_vector

MemoryEntityType = Literal[
    "project_fact",
    "research_step",
    "verified_claim",
    "failed_route",
]
_TOKEN_PATTERN = re.compile(r"[a-z0-9_./+-]{2,}|[\u3400-\u9fff]{2,}", re.I)


@dataclass(frozen=True, slots=True)
class MemoryRecallFilters:
    entity_types: tuple[MemoryEntityType, ...] = ("project_fact", "research_step")
    categories: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ("active",)
    min_confidence: float = 0.0
    created_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class MemoryRecallItem:
    entity_type: MemoryEntityType
    entity_id: UUID
    text: str
    category: str | None
    status: str
    confidence: float
    importance: float
    created_at: datetime
    dense_score: float
    lexical_score: float
    recent_score: float
    importance_score: float
    fused_score: float
    routes: tuple[str, ...]
    source: dict[str, Any]
    related: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryRecallResult:
    items: tuple[MemoryRecallItem, ...]
    indexed: int
    embedding_model: str
    strategy: str = "recent_important_semantic_mmr_v1"


@dataclass(frozen=True, slots=True)
class _Candidate:
    entity_type: MemoryEntityType
    entity_id: UUID
    text: str
    category: str | None
    status: str
    confidence: float
    importance: float
    created_at: datetime
    source: dict[str, Any]
    vector: np.ndarray
    dense_score: float
    lexical_score: float
    recent_score: float
    fused_score: float
    routes: tuple[str, ...]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(value)}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _recency(value: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - _as_utc(value)).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 30.0)


def _step_text(value: ResearchStepMemory) -> str:
    parts = [value.step_type, value.input_summary, value.decision, value.rationale]
    parts.extend(value.new_claims)
    parts.extend(value.resolved_claims)
    parts.extend(value.unresolved_claims)
    return "\n".join(part for part in parts if part).strip()[:12000]


def _claim_text(value: ResearchClaimTarget) -> str:
    return "\n".join(
        part
        for part in (
            value.statement,
            value.falsifiable_prediction,
            value.unresolved_reason or "",
        )
        if part
    )[:12000]


def _route_text(value: RetrievalRouteMemory) -> str:
    return "\n".join(
        part
        for part in (
            value.query,
            value.tool,
            value.failure_reason,
            value.retry_reason,
        )
        if part
    )[:12000]


class HybridMemoryRetrievalService:
    """Hybrid recall over a replaceable, non-Evidence durable-memory index."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_service: SentenceTransformerEmbeddingService,
    ) -> None:
        self._session = session
        self._embedding = embedding_service

    async def refresh_project(self, project_id: UUID) -> int:
        """Rebuild the derived index, skipping rescans when memory is unchanged.

        Embeddings are already incremental (content-hash gated). The fast path
        additionally skips the full-table scans and hashing when no memory
        entity changed since the last index build, tracked via the maximum
        ``updated_at`` across all memory tables and the index itself.
        """
        memory_max = await self._latest_memory_update(project_id)
        index_max = await self._latest_index_update(project_id)
        if (
            memory_max is not None
            and index_max is not None
            and _as_utc(memory_max) < _as_utc(index_max)
        ):
            # Strictly earlier: SQLite CURRENT_TIMESTAMP has second precision, so
            # an equal timestamp forces a rescan instead of risking a stale index.
            return 0
        facts = list(
            (
                await self._session.scalars(
                    select(ProjectFact).where(
                        ProjectFact.project_id == project_id,
                        ProjectFact.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        steps = list(
            (
                await self._session.scalars(
                    select(ResearchStepMemory).where(
                        ResearchStepMemory.project_id == project_id
                    )
                )
            ).all()
        )
        claims = list(
            (
                await self._session.scalars(
                    select(ResearchClaimTarget)
                    .join(
                        ResearchRun,
                        ResearchRun.id == ResearchClaimTarget.research_run_id,
                    )
                    .where(
                        ResearchRun.project_id == project_id,
                        ResearchRun.deleted_at.is_(None),
                        ResearchClaimTarget.status == "supported",
                        ResearchClaimTarget.verification_label == "entailment",
                    )
                )
            ).all()
        )
        failed_routes = list(
            (
                await self._session.scalars(
                    select(RetrievalRouteMemory).where(
                        RetrievalRouteMemory.project_id == project_id
                    )
                )
            ).all()
        )
        documents: list[tuple[MemoryEntityType, UUID, str]] = [
            ("project_fact", value.id, value.statement) for value in facts
        ]
        documents.extend(
            ("research_step", value.id, _step_text(value)) for value in steps
        )
        documents.extend(
            ("verified_claim", value.id, _claim_text(value)) for value in claims
        )
        documents.extend(
            ("failed_route", value.id, _route_text(value)) for value in failed_routes
        )
        existing = {
            (value.entity_type, value.entity_id): value
            for value in (
                await self._session.scalars(
                    select(MemoryDerivedIndex).where(
                        MemoryDerivedIndex.project_id == project_id,
                        MemoryDerivedIndex.model_name == self._embedding.model_name,
                    )
                )
            ).all()
        }
        active_keys = {(entity_type, entity_id) for entity_type, entity_id, _ in documents}
        stale_ids = [
            value.id
            for key, value in existing.items()
            if key not in active_keys
        ]
        if stale_ids:
            await self._session.execute(
                delete(MemoryDerivedIndex).where(MemoryDerivedIndex.id.in_(stale_ids))
            )
        pending: list[tuple[MemoryEntityType, UUID, str, str]] = []
        for entity_type, entity_id, text in documents:
            content_hash = _hash(text)
            current = existing.get((entity_type, entity_id))
            if current is not None and current.content_hash == content_hash:
                continue
            pending.append((entity_type, entity_id, text, content_hash))
        indexed = 0
        for offset in range(0, len(pending), self._embedding.batch_size):
            batch = pending[offset : offset + self._embedding.batch_size]
            vectors = await self._embedding.embed_passages([value[2] for value in batch])
            for (entity_type, entity_id, text, content_hash), vector in zip(
                batch, vectors, strict=True
            ):
                current = existing.get((entity_type, entity_id))
                blob = encode_float32_vector(
                    vector,
                    expected_dimension=self._embedding.dimension,
                )
                if current is None:
                    self._session.add(
                        MemoryDerivedIndex(
                            project_id=project_id,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            content_hash=content_hash,
                            search_text=text,
                            model_name=self._embedding.model_name,
                            dimension=self._embedding.dimension,
                            vector=blob,
                        )
                    )
                else:
                    current.content_hash = content_hash
                    current.search_text = text
                    current.dimension = self._embedding.dimension
                    current.vector = blob
                indexed += 1
        await self._session.flush()
        # Record freshness even for unchanged rows so the next recall can take
        # the fast path instead of rescanning after every non-text change.
        await self._session.execute(
            update(MemoryDerivedIndex)
            .where(
                MemoryDerivedIndex.project_id == project_id,
                MemoryDerivedIndex.model_name == self._embedding.model_name,
            )
            .values(updated_at=func.now())
        )
        return indexed

    async def _latest_memory_update(self, project_id: UUID) -> datetime | None:
        values: list[datetime | None] = [
            await self._session.scalar(
                select(func.max(ProjectFact.updated_at)).where(
                    ProjectFact.project_id == project_id,
                    ProjectFact.deleted_at.is_(None),
                )
            ),
            await self._session.scalar(
                select(func.max(ResearchStepMemory.updated_at)).where(
                    ResearchStepMemory.project_id == project_id
                )
            ),
            await self._session.scalar(
                select(func.max(ResearchClaimTarget.updated_at))
                .join(ResearchRun, ResearchRun.id == ResearchClaimTarget.research_run_id)
                .where(
                    ResearchRun.project_id == project_id,
                    ResearchRun.deleted_at.is_(None),
                )
            ),
            await self._session.scalar(
                select(func.max(ResearchRun.updated_at)).where(
                    ResearchRun.project_id == project_id,
                    ResearchRun.deleted_at.is_(None),
                )
            ),
            await self._session.scalar(
                select(func.max(RetrievalRouteMemory.updated_at)).where(
                    RetrievalRouteMemory.project_id == project_id
                )
            ),
        ]
        present = [value for value in values if isinstance(value, datetime)]
        return max(present) if present else None

    async def _latest_index_update(self, project_id: UUID) -> datetime | None:
        value: datetime | None = await self._session.scalar(
            select(func.max(MemoryDerivedIndex.updated_at)).where(
                MemoryDerivedIndex.project_id == project_id,
                MemoryDerivedIndex.model_name == self._embedding.model_name,
            )
        )
        return value

    async def recall(
        self,
        *,
        project_id: UUID,
        query: str,
        filters: MemoryRecallFilters | None = None,
        limit: int = 12,
        candidate_limit: int = 80,
    ) -> MemoryRecallResult:
        await self.refresh_project(project_id)
        filters = filters or MemoryRecallFilters()
        rows = list(
            (
                await self._session.scalars(
                    select(MemoryDerivedIndex).where(
                        MemoryDerivedIndex.project_id == project_id,
                        MemoryDerivedIndex.entity_type.in_(filters.entity_types),
                        MemoryDerivedIndex.model_name == self._embedding.model_name,
                        MemoryDerivedIndex.dimension == self._embedding.dimension,
                    )
                )
            ).all()
        )
        if not rows:
            return MemoryRecallResult((), 0, self._embedding.model_name)
        facts = {
            value.id: value
            for value in (
                await self._session.scalars(
                    select(ProjectFact).where(ProjectFact.project_id == project_id)
                )
            ).all()
        }
        steps = {
            value.id: value
            for value in (
                await self._session.scalars(
                    select(ResearchStepMemory).where(
                        ResearchStepMemory.project_id == project_id
                    )
                )
            ).all()
        }
        claims = {
            value.id: value
            for value in (
                await self._session.scalars(
                    select(ResearchClaimTarget)
                    .join(
                        ResearchRun,
                        ResearchRun.id == ResearchClaimTarget.research_run_id,
                    )
                    .where(ResearchRun.project_id == project_id)
                )
            ).all()
        }
        failed_routes = {
            value.id: value
            for value in (
                await self._session.scalars(
                    select(RetrievalRouteMemory).where(
                        RetrievalRouteMemory.project_id == project_id
                    )
                )
            ).all()
        }
        query_vector = np.asarray(
            (await self._embedding.embed_queries([query]))[0],
            dtype=np.float32,
        )
        query_tokens = _tokens(query)
        now = datetime.now(UTC)
        candidates: list[_Candidate] = []
        for row in rows:
            if row.entity_type == "project_fact":
                fact = facts.get(row.entity_id)
                if fact is None or fact.deleted_at is not None:
                    continue
                if filters.categories and fact.category not in filters.categories:
                    continue
                if filters.statuses and fact.status not in filters.statuses:
                    continue
                if fact.confidence < filters.min_confidence:
                    continue
                created_at = fact.created_at
                category = fact.category
                status = fact.status
                confidence = fact.confidence
                importance = fact.importance
                source: dict[str, Any] = {
                    "source_type": fact.source_type,
                    "source_id": str(fact.source_id),
                    "source_locator": fact.source_locator,
                }
            elif row.entity_type == "research_step":
                step = steps.get(row.entity_id)
                if step is None:
                    continue
                if filters.categories and step.step_type not in filters.categories:
                    continue
                created_at = step.created_at
                category = step.step_type
                status = step.status
                confidence = 1.0
                importance = min(1.0, 0.45 + 0.08 * len(step.evidence_ids))
                source = {
                    "workflow_id": str(step.workflow_id),
                    "research_run_id": (
                        str(step.research_run_id) if step.research_run_id else None
                    ),
                    "brainstorm_session_id": (
                        str(step.brainstorm_session_id)
                        if step.brainstorm_session_id
                        else None
                    ),
                    "step_number": step.step_number,
                    "round_number": step.round_number,
                    "historical_evidence_count": len(step.evidence_ids),
                    "context_role": "planning_experience",
                    "evidence_eligible": False,
                }
            elif row.entity_type == "verified_claim":
                claim = claims.get(row.entity_id)
                if (
                    claim is None
                    or claim.status != "supported"
                    or claim.verification_label != "entailment"
                ):
                    continue
                if filters.categories and claim.claim_type not in filters.categories:
                    continue
                if (claim.verification_confidence or 0.0) < filters.min_confidence:
                    continue
                created_at = claim.created_at
                category = claim.claim_type
                status = claim.status
                confidence = claim.verification_confidence or 0.0
                importance = claim.priority
                source = {
                    "research_run_id": str(claim.research_run_id),
                    "claim_key": claim.claim_key,
                    "historical_evidence_ids": list(claim.evidence_ids),
                    "historical_contradicting_evidence_ids": list(
                        claim.contradicting_evidence_ids
                    ),
                    "verification_label": claim.verification_label,
                    "verification_confidence": claim.verification_confidence,
                    "verification_model": claim.verification_model,
                    "verification_version": claim.verification_version,
                    "verification_checks": claim.verification_checks,
                    "context_role": "verified_claim_prior",
                    "evidence_eligible": False,
                    "citation_requires_current_retrieval": True,
                }
            else:
                route = failed_routes.get(row.entity_id)
                if route is None:
                    continue
                if filters.categories and route.tool not in filters.categories:
                    continue
                created_at = route.created_at
                category = route.tool
                status = route.status
                confidence = 1.0
                importance = 0.8 if route.retry_worthy else 0.65
                source = {
                    "workflow_id": str(route.workflow_id),
                    "workflow_kind": route.workflow_kind,
                    "tool": route.tool,
                    "scope": route.scope,
                    "result_count": route.result_count,
                    "failure_reason": route.failure_reason,
                    "retry_worthy": route.retry_worthy,
                    "retry_reason": route.retry_reason,
                    "context_role": "route_failure_experience",
                    "evidence_eligible": False,
                }
            if filters.created_after and _as_utc(created_at) < _as_utc(
                filters.created_after
            ):
                continue
            vector = decode_float32_vector(
                row.vector,
                expected_dimension=self._embedding.dimension,
            )
            dense = max(0.0, float(vector @ query_vector))
            tokens = _tokens(row.search_text)
            lexical = (
                len(query_tokens & tokens) / max(1, len(query_tokens))
                if query_tokens
                else 0.0
            )
            recent = _recency(created_at, now)
            fused = 0.45 * dense + 0.20 * lexical + 0.20 * importance + 0.15 * recent
            routes = tuple(
                route
                for route, score in (
                    ("semantic", dense),
                    ("keyword", lexical),
                    ("important", importance),
                    ("recent", recent),
                )
                if score > 0.05
            )
            candidates.append(
                _Candidate(
                    entity_type=row.entity_type,  # type: ignore[arg-type]
                    entity_id=row.entity_id,
                    text=row.search_text,
                    category=category,
                    status=status,
                    confidence=confidence,
                    importance=importance,
                    created_at=created_at,
                    source=source,
                    vector=vector,
                    dense_score=dense,
                    lexical_score=lexical,
                    recent_score=recent,
                    fused_score=fused,
                    routes=routes,
                )
            )
        ranked = sorted(candidates, key=lambda value: value.fused_score, reverse=True)[
            : max(candidate_limit, limit)
        ]
        selected = self._mmr(ranked, limit=limit)
        related = await self._related_facts(project_id, selected, facts)
        items = tuple(
            MemoryRecallItem(
                entity_type=value.entity_type,
                entity_id=value.entity_id,
                text=value.text,
                category=value.category,
                status=value.status,
                confidence=value.confidence,
                importance=value.importance,
                created_at=value.created_at,
                dense_score=value.dense_score,
                lexical_score=value.lexical_score,
                recent_score=value.recent_score,
                importance_score=value.importance,
                fused_score=value.fused_score,
                routes=value.routes,
                source=value.source,
                related=tuple(related.get(value.entity_id, [])),
            )
            for value in selected
        )
        return MemoryRecallResult(items, len(rows), self._embedding.model_name)

    @staticmethod
    def _mmr(candidates: list[_Candidate], *, limit: int) -> list[_Candidate]:
        selected: list[_Candidate] = []
        remaining = list(candidates)
        while remaining and len(selected) < max(1, limit):
            best = max(
                remaining,
                key=lambda candidate: (
                    0.72 * candidate.fused_score
                    - 0.28
                    * max(
                        (
                            float(candidate.vector @ value.vector)
                            for value in selected
                        ),
                        default=0.0,
                    )
                ),
            )
            selected.append(best)
            remaining.remove(best)
        return selected

    async def _related_facts(
        self,
        project_id: UUID,
        selected: list[_Candidate],
        facts: dict[UUID, ProjectFact],
    ) -> dict[UUID, list[dict[str, Any]]]:
        fact_ids = {
            value.entity_id
            for value in selected
            if value.entity_type == "project_fact"
        }
        if not fact_ids:
            return {}
        relations = list(
            (
                await self._session.scalars(
                    select(ProjectFactRelation).where(
                        ProjectFactRelation.project_id == project_id,
                        (
                            ProjectFactRelation.source_fact_id.in_(fact_ids)
                            | ProjectFactRelation.target_fact_id.in_(fact_ids)
                        ),
                    )
                )
            ).all()
        )
        output: dict[UUID, list[dict[str, Any]]] = {}
        for relation in relations:
            for owner, related_id in (
                (relation.source_fact_id, relation.target_fact_id),
                (relation.target_fact_id, relation.source_fact_id),
            ):
                if owner not in fact_ids:
                    continue
                fact = facts.get(related_id)
                if fact is None:
                    continue
                output.setdefault(owner, []).append(
                    {
                        "relation": relation.relation_type,
                        "fact_id": str(fact.id),
                        "statement": fact.statement,
                        "status": fact.status,
                        "category": fact.category,
                    }
                )
        return output
