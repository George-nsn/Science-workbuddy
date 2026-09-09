import pytest

from science_buddy.evaluation import (
    ann_recall_at_k,
    average_precision,
    hit_at_k,
    ndcg_at_k,
    percentile,
    precision_at_k,
    reciprocal_rank,
    score_query,
)


def test_retrieval_metrics_reward_early_relevant_results() -> None:
    ranked = ["irrelevant", "gold-a", "gold-b"]
    relevant = {"gold-a", "gold-b"}

    metrics = score_query(ranked, relevant, latency_ms=12.5)

    assert metrics.recall_at_5 == 1.0
    assert metrics.reciprocal_rank == 0.5
    assert 0 < metrics.ndcg_at_10 < 1
    assert metrics.latency_ms == 12.5


def test_ndcg_is_one_for_ideal_binary_ranking() -> None:
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 10) == pytest.approx(1.0)
    assert reciprocal_rank(["a"], {"a"}) == 1.0


def test_vector_backend_comparison_reports_ann_recall_and_percentiles() -> None:
    exact = [str(index) for index in range(20)]
    candidate = [*exact[:9], "missing", *exact[10:]]

    assert ann_recall_at_k(exact, candidate, 10) == 0.9
    assert ann_recall_at_k(exact, exact, 20) == 1.0
    assert percentile([30.0, 10.0, 20.0], 0.50) == 20.0
    assert percentile([30.0, 10.0, 20.0], 0.95) == 30.0


def test_precision_and_hit_metrics_are_bounded() -> None:
    ranked = ["gold-a", "irrelevant", "irrelevant", "gold-b", "irrelevant"]
    relevant = {"gold-a", "gold-b"}

    assert precision_at_k(ranked, relevant, 5) == 0.4
    # Short lists divide by the available length, not by k.
    assert precision_at_k(ranked, relevant, 10) == 0.4
    assert precision_at_k([], relevant, 5) == 0.0
    assert precision_at_k(ranked, set(), 5) == 0.0
    assert hit_at_k(ranked, relevant, 5) == 1.0
    assert hit_at_k(["irrelevant"], relevant, 5) == 0.0


def test_average_precision_rewards_earlier_relevant_items() -> None:
    relevant = {"gold-a", "gold-b"}
    assert average_precision(["gold-a", "gold-b"], relevant) == 1.0
    assert average_precision(["gold-a", "x", "gold-b"], relevant) == pytest.approx(
        (1.0 + 2 / 3) / 2
    )
    assert average_precision([], relevant) == 0.0
    assert average_precision(["x"], set()) == 0.0
