import math
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.domain.providers import ModelCallUsage
from science_buddy.infrastructure.models import UsageEvent

DashboardRange = Literal["7d", "30d", "90d", "all"]
DashboardGranularity = Literal["day", "week", "month"]


def range_start(value: DashboardRange, now: datetime | None = None) -> datetime | None:
    current = now or datetime.now(UTC)
    days = {"7d": 7, "30d": 30, "90d": 90}.get(value)
    return current - timedelta(days=days) if days else None


def bucket_label(value: datetime, granularity: DashboardGranularity) -> str:
    if granularity == "month":
        return value.strftime("%Y-%m")
    if granularity == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    return value.strftime("%Y-%m-%d")


def _latency_percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def usage_summary(events: list[UsageEvent]) -> dict[str, object]:
    model_events = [event for event in events if event.event_type == "model"]
    retrieval_events = [event for event in events if event.event_type == "retrieval"]
    hits = sum(1 for event in retrieval_events if event.cache_level not in {None, "miss"})
    known_cost = sum(event.cost_usd or 0 for event in model_events)
    latencies = [event.latency_ms for event in model_events if event.latency_ms > 0]
    return {
        "total_tokens": sum(event.total_tokens or 0 for event in model_events),
        "prompt_tokens": sum(event.prompt_tokens or 0 for event in model_events),
        "completion_tokens": sum(event.completion_tokens or 0 for event in model_events),
        "cached_tokens": sum(event.cached_tokens or 0 for event in model_events),
        "request_count": len(model_events),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "latency_p50_ms": round(_latency_percentile(latencies, 0.50), 1),
        "latency_p95_ms": round(_latency_percentile(latencies, 0.95), 1),
        "retrieval_count": len(retrieval_events),
        "cache_hits": hits,
        "cache_hit_rate": hits / len(retrieval_events) if retrieval_events else 0.0,
        "known_cost_usd": known_cost,
        "cost_coverage_rate": (
            sum(1 for event in model_events if event.cost_usd is not None) / len(model_events)
            if model_events
            else 0.0
        ),
        "estimated_token_events": sum(
            1 for event in model_events if event.token_count_estimated
        ),
    }


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_model_events(
        self,
        *,
        project_id: UUID,
        events: list[ModelCallUsage],
        session_id: UUID | None = None,
        message_id: UUID | None = None,
        research_run_id: UUID | None = None,
        turn_number: int | None = None,
    ) -> None:
        self._session.add_all(
            [
                UsageEvent(
                    project_id=project_id,
                    session_id=session_id,
                    message_id=message_id,
                    research_run_id=research_run_id,
                    turn_number=turn_number,
                    event_type="model",
                    operation=event.operation,
                    provider=event.provider,
                    model_name=event.model,
                    model_depth=event.depth,
                    prompt_tokens=event.prompt_tokens,
                    completion_tokens=event.completion_tokens,
                    total_tokens=event.total_tokens,
                    cached_tokens=event.cached_tokens,
                    token_count_estimated=event.token_count_estimated,
                    cost_usd=event.cost_usd,
                    cost_source=event.cost_source,
                    latency_ms=event.latency_ms,
                )
                for event in events
            ]
        )
        if events:
            await self._session.flush()

    async def record_retrieval(
        self,
        *,
        project_id: UUID,
        operation: str,
        cache_level: str,
        session_id: UUID | None = None,
        turn_number: int | None = None,
    ) -> None:
        self._session.add(
            UsageEvent(
                project_id=project_id,
                session_id=session_id,
                turn_number=turn_number,
                event_type="retrieval",
                operation=operation,
                cache_level=cache_level,
            )
        )

    async def list_events(
        self,
        project_id: UUID,
        *,
        period: DashboardRange = "30d",
    ) -> list[UsageEvent]:
        statement = select(UsageEvent).where(UsageEvent.project_id == project_id)
        start = range_start(period)
        if start is not None:
            statement = statement.where(UsageEvent.created_at >= start)
        return list(
            (
                await self._session.scalars(
                    statement.order_by(UsageEvent.created_at.asc())
                )
            ).all()
        )

    async def session_events(self, session_id: UUID) -> list[UsageEvent]:
        return list(
            (
                await self._session.scalars(
                    select(UsageEvent)
                    .where(UsageEvent.session_id == session_id)
                    .order_by(UsageEvent.created_at.asc())
                )
            ).all()
        )

    async def dashboard(
        self,
        project_id: UUID,
        *,
        period: DashboardRange,
        granularity: DashboardGranularity,
    ) -> dict[str, object]:
        events = await self.list_events(project_id, period=period)
        buckets: dict[str, list[UsageEvent]] = {}
        for event in events:
            buckets.setdefault(bucket_label(event.created_at, granularity), []).append(event)
        models: dict[tuple[str, str], list[UsageEvent]] = {}
        for event in events:
            if event.event_type == "model":
                key = (event.provider or "unknown", event.model_name or "unknown")
                models.setdefault(key, []).append(event)
        return {
            "summary": usage_summary(events),
            "series": [
                {"bucket": label, **usage_summary(values)}
                for label, values in sorted(buckets.items())
            ],
            "models": [
                {
                    "provider": provider,
                    "model": model,
                    **usage_summary(values),
                }
                for (provider, model), values in sorted(
                    models.items(),
                    key=lambda item: sum(event.total_tokens for event in item[1]),
                    reverse=True,
                )
            ],
        }