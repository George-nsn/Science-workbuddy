import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    ProjectFact,
    ProjectFactRelation,
    ResearchRun,
)
from science_buddy.services.research import ResearchResult


@dataclass(frozen=True, slots=True)
class PublishResult:
    created: int
    reused: int
    conflicts: int
    fact_ids: tuple[UUID, ...]
    published_at: datetime


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(value: str) -> set[str]:
    return {item for item in _normalize(value).replace("/", " ").split() if len(item) > 1}


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


def _is_conflict(left: str, right: str) -> bool:
    conflict_markers = {"not", "no", "decrease", "increase", "降低", "升高", "无", "不"}
    left_markers = conflict_markers & _tokens(left)
    right_markers = conflict_markers & _tokens(right)
    return _similarity(left, right) >= 0.55 and left_markers != right_markers


class ResearchFactPublisher:
    """Publish verified research claims only after explicit user confirmation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(
        self,
        *,
        run: ResearchRun,
        published_by: str,
    ) -> PublishResult:
        if run.deleted_at is not None:
            raise ValueError("Deleted research runs cannot publish facts")
        if run.facts_published_at is not None:
            facts = list(
                (
                    await self._session.scalars(
                        select(ProjectFact).where(
                            ProjectFact.project_id == run.project_id,
                            ProjectFact.source_type == "research_run",
                            ProjectFact.source_id == run.id,
                        )
                    )
                ).all()
            )
            return PublishResult(
                created=0,
                reused=len(facts),
                conflicts=0,
                fact_ids=tuple(value.id for value in facts),
                published_at=run.facts_published_at,
            )
        result = ResearchResult.model_validate(run.result)
        if not result.claims:
            raise ValueError("Research run has no verified claims to publish")
        existing = list(
            (
                await self._session.scalars(
                    select(ProjectFact).where(
                        ProjectFact.project_id == run.project_id,
                        ProjectFact.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        created = 0
        reused = 0
        conflicts = 0
        fact_ids: list[UUID] = []
        for index, claim in enumerate(result.claims, start=1):
            statement = " ".join(claim.statement.split())
            statement_hash = _hash(_normalize(statement))
            fact = await self._session.scalar(
                select(ProjectFact).where(
                    ProjectFact.project_id == run.project_id,
                    ProjectFact.category == "verified_research_claim",
                    ProjectFact.statement_hash == statement_hash,
                    ProjectFact.source_type == "research_run",
                    ProjectFact.source_id == run.id,
                )
            )
            evidence_ids = [value.evidence_id for value in claim.evidence]
            if fact is None:
                fact = ProjectFact(
                    project_id=run.project_id,
                    category="verified_research_claim",
                    statement=statement,
                    statement_hash=statement_hash,
                    source_type="research_run",
                    source_id=run.id,
                    source_session_id=None,
                    source_locator={
                        "run_id": str(run.id),
                        "claim_index": index,
                        "evidence_ids": evidence_ids,
                        "relation": claim.relation,
                        "semantic_verification": claim.semantic_verification,
                        "published_by": published_by,
                    },
                    confidence=0.92,
                    importance=0.92,
                    status="active",
                )
                self._session.add(fact)
                await self._session.flush()
                created += 1
            else:
                reused += 1
            fact_ids.append(fact.id)
            for prior in existing:
                relation: str | None = None
                if _normalize(prior.statement) == _normalize(statement):
                    relation = "synonym_of"
                elif _is_conflict(prior.statement, statement):
                    relation = "conflicts_with"
                if relation is None or prior.id == fact.id:
                    continue
                relation_row = await self._session.scalar(
                    select(ProjectFactRelation).where(
                        ProjectFactRelation.source_fact_id == fact.id,
                        ProjectFactRelation.relation_type == relation,
                        ProjectFactRelation.target_fact_id == prior.id,
                    )
                )
                if relation_row is None:
                    self._session.add(
                        ProjectFactRelation(
                            project_id=run.project_id,
                            source_fact_id=fact.id,
                            target_fact_id=prior.id,
                            relation_type=relation,
                            provenance={"run_id": str(run.id)},
                        )
                    )
                    if relation == "conflicts_with":
                        conflicts += 1
        published_at = datetime.now(UTC)
        run.facts_published_at = published_at
        run.facts_published_by = published_by
        await self._session.flush()
        return PublishResult(
            created=created,
            reused=reused,
            conflicts=conflicts,
            fact_ids=tuple(fact_ids),
            published_at=published_at,
        )
