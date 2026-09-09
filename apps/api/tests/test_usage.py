from datetime import UTC, datetime
from uuid import uuid4

from science_buddy.infrastructure.models import UsageEvent
from science_buddy.services.usage import bucket_label, usage_summary


def test_usage_summary_keeps_cost_and_estimation_transparent() -> None:
    project_id = uuid4()
    events = [
        UsageEvent(
            project_id=project_id,
            event_type="model",
            operation="brainstorm.coordinator",
            provider="openai",
            model_name="test-model",
            model_depth="balanced",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_tokens=30,
            token_count_estimated=False,
            cost_usd=0.012,
            cost_source="upstream",
            latency_ms=1200.0,
        ),
        UsageEvent(
            project_id=project_id,
            event_type="model",
            operation="brainstorm.critic",
            provider="local",
            model_name="test-model",
            model_depth="deep",
            prompt_tokens=50,
            completion_tokens=10,
            total_tokens=60,
            cached_tokens=0,
            token_count_estimated=True,
            cost_usd=None,
            cost_source="unavailable",
            latency_ms=800.0,
        ),
        UsageEvent(
            project_id=project_id,
            event_type="model",
            operation="brainstorm.critic",
            provider="local",
            model_name="test-model",
            model_depth="deep",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            token_count_estimated=True,
            cost_usd=None,
            cost_source="unavailable",
            latency_ms=400.0,
        ),
        UsageEvent(
            project_id=project_id,
            event_type="retrieval",
            operation="brainstorm.retrieval",
            cache_level="l1",
        ),
        UsageEvent(
            project_id=project_id,
            event_type="retrieval",
            operation="brainstorm.retrieval",
            cache_level="miss",
        ),
    ]

    summary = usage_summary(events)

    assert summary["total_tokens"] == 195
    assert summary["cache_hit_rate"] == 0.5
    assert summary["known_cost_usd"] == 0.012
    assert summary["cost_coverage_rate"] == 1 / 3
    assert summary["estimated_token_events"] == 2
    assert summary["avg_latency_ms"] == 800.0
    assert summary["latency_p50_ms"] == 800.0
    assert summary["latency_p95_ms"] == 1200.0
    assert bucket_label(datetime(2026, 8, 4, tzinfo=UTC), "week") == "2026-W32"


def test_usage_summary_reports_zero_latency_when_unmeasured() -> None:
    project_id = uuid4()
    events = [
        UsageEvent(
            project_id=project_id,
            event_type="model",
            operation="research.synthesis",
            provider="openai",
            model_name="test-model",
            total_tokens=100,
            latency_ms=0.0,
        )
    ]

    summary = usage_summary(events)

    assert summary["avg_latency_ms"] == 0.0
    assert summary["latency_p50_ms"] == 0.0
    assert summary["latency_p95_ms"] == 0.0