from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.services.embeddings import (
    EmbeddingUnavailableError,
    get_embedding_service,
)
from science_buddy.services.memory_retrieval import (
    HybridMemoryRetrievalService,
    MemoryEntityType,
    MemoryRecallFilters,
)

MemoryConsumer = Literal["brainstorm", "research_planner", "synthesis"]
MemoryContextRole = Literal[
    "project_context",
    "confirmed_project_fact",
    "planning_experience",
    "verified_claim_prior",
    "route_failure_experience",
]
_CONSUMER_POLICIES: dict[MemoryConsumer, tuple[MemoryEntityType, ...]] = {
    "brainstorm": ("project_fact", "research_step", "failed_route"),
    "research_planner": (
        "project_fact",
        "research_step",
        "verified_claim",
        "failed_route",
    ),
    "synthesis": (
        "project_fact",
        "verified_claim",
        "research_step",
        "failed_route",
    ),
}


@dataclass(frozen=True, slots=True)
class MemoryContextItem:
    memory_type: MemoryEntityType
    entity_id: UUID
    role: MemoryContextRole
    text: str
    category: str | None
    status: str
    confidence: float
    importance: float
    fused_score: float
    routes: tuple[str, ...]
    source: dict[str, Any]
    related: tuple[dict[str, Any], ...]
    created_at: datetime
    evidence_eligible: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "memory_type": self.memory_type,
            "entity_id": str(self.entity_id),
            "role": self.role,
            "text": self.text,
            "category": self.category,
            "status": self.status,
            "confidence": self.confidence,
            "importance": self.importance,
            "fused_score": self.fused_score,
            "routes": list(self.routes),
            "source": self.source,
            "related": list(self.related),
            "created_at": self.created_at.isoformat(),
            "evidence_eligible": False,
        }


@dataclass(frozen=True, slots=True)
class MemoryContextResult:
    consumer: MemoryConsumer
    allowed_memory_types: tuple[MemoryEntityType, ...]
    items: tuple[MemoryContextItem, ...]
    indexed: int
    embedding_model: str
    strategy: str = "consumer_policy_hybrid_memory_v1"

    def to_payload(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer,
            "allowed_memory_types": list(self.allowed_memory_types),
            "strategy": self.strategy,
            "indexed": self.indexed,
            "embedding_model": self.embedding_model,
            "evidence_boundary": (
                "Memory Context supports planning and continuity only. "
                "It never becomes current-workflow Evidence."
            ),
            "items": [value.to_payload() for value in self.items],
        }


class MemoryContextService:
    """One consumer-policy API for planning memory across all research workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def allowed_types(consumer: MemoryConsumer) -> tuple[MemoryEntityType, ...]:
        return _CONSUMER_POLICIES[consumer]

    async def recall(
        self,
        *,
        project_id: UUID,
        query: str,
        consumer: MemoryConsumer,
        memory_types: tuple[MemoryEntityType, ...] | None = None,
        categories: tuple[str, ...] = (),
        statuses: tuple[str, ...] = ("active",),
        min_confidence: float = 0.0,
        created_after: datetime | None = None,
        limit: int = 16,
    ) -> MemoryContextResult:
        allowed = self.allowed_types(consumer)
        selected_types = memory_types or allowed
        unsupported = set(selected_types) - set(allowed)
        if unsupported:
            values = ", ".join(sorted(unsupported))
            raise ValueError(f"Memory types not allowed for {consumer}: {values}")
        if not query.strip():
            return MemoryContextResult(consumer, selected_types, (), 0, "not_loaded")
        try:
            result = await HybridMemoryRetrievalService(
                self._session,
                embedding_service=get_embedding_service(),
            ).recall(
                project_id=project_id,
                query=query,
                filters=MemoryRecallFilters(
                    entity_types=selected_types,
                    categories=categories,
                    statuses=statuses,
                    min_confidence=min_confidence,
                    created_after=created_after,
                ),
                limit=limit,
            )
        except EmbeddingUnavailableError:
            return MemoryContextResult(
                consumer,
                selected_types,
                (),
                0,
                "unavailable",
                strategy="consumer_policy_memory_unavailable",
            )
        items = [self._context_item(value) for value in result.items]
        if consumer == "synthesis":
            priority = {
                "confirmed_project_fact": 0,
                "verified_claim_prior": 1,
                "project_context": 2,
                "planning_experience": 3,
                "route_failure_experience": 4,
            }
            items.sort(key=lambda value: (priority[value.role], -value.fused_score))
        return MemoryContextResult(
            consumer,
            selected_types,
            tuple(items),
            result.indexed,
            result.embedding_model,
        )

    @staticmethod
    def _context_item(value: Any) -> MemoryContextItem:
        source = dict(value.source)
        if value.entity_type == "project_fact":
            locator = dict(source.get("source_locator") or {})
            historical_ids = locator.pop("evidence_ids", None)
            if historical_ids:
                locator["historical_evidence_ids"] = historical_ids
            source["source_locator"] = locator
            role: MemoryContextRole = (
                "confirmed_project_fact"
                if value.category == "verified_research_claim"
                and source.get("source_type") == "research_run"
                else "project_context"
            )
        elif value.entity_type == "research_step":
            role = "planning_experience"
        elif value.entity_type == "verified_claim":
            role = "verified_claim_prior"
        else:
            role = "route_failure_experience"
        source["context_role"] = role
        source["evidence_eligible"] = False
        return MemoryContextItem(
            memory_type=value.entity_type,
            entity_id=value.entity_id,
            role=role,
            text=value.text,
            category=value.category,
            status=value.status,
            confidence=value.confidence,
            importance=value.importance,
            fused_score=value.fused_score,
            routes=value.routes,
            source=source,
            related=value.related,
            created_at=value.created_at,
        )
