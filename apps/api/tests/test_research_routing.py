import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.services.adaptive_retrieval import routed_query_plan
from science_buddy.services.query_planning import DeterministicQueryPlanner
from science_buddy.services.research_routing import (
    ControlledResearchRetriever,
    ControlledRetrievalStep,
    DeterministicResearchRouter,
)
from science_buddy.services.retrieval import RetrievalConfig
from science_buddy.services.retrieval_cache import retrieval_cache_key_payload


def retrieval_config(**overrides: Any) -> RetrievalConfig:
    values: dict[str, Any] = {
        "version": "routing-test",
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


@pytest.mark.parametrize(
    ("question", "question_type", "strategy"),
    [
        (
            "Pembrolizumab versus nivolumab 哪种治疗总体生存更好？",
            "comparison",
            "decomposition",
        ),
        ("BRAF V600E 如何通过 MAPK 通路驱动肿瘤？", "mechanism", "direct"),
        (
            "在晚期肺癌患者中，免疫治疗相比化疗能否改善生存结局？",
            "pico",
            "decomposition",
        ),
        ("单细胞 RNA 测序的质控和统计方法如何设计？", "methodology", "direct"),
        (
            "对噬菌体治疗耐药菌的证据开展系统综述和荟萃分析",
            "systematic_review",
            "deep_research",
        ),
        ("近五年 CRISPR 抗噬菌体系统的最新研究进展", "latest_progress", "deep_research"),
    ],
)
def test_router_classifies_research_intent_and_strategy(
    question: str,
    question_type: str,
    strategy: str,
) -> None:
    decision = DeterministicResearchRouter().route(question)

    assert decision.question_type == question_type
    assert decision.strategy == strategy
    assert decision.reason_codes
    assert 1 <= len(decision.subqueries) <= 4
    assert decision.max_followup_rounds == (1 if strategy == "deep_research" else 0)


def test_identifier_and_explicit_strategy_are_bounded() -> None:
    router = DeterministicResearchRouter()

    identifier = router.route("PMID: 12345678", identifier=True)
    forced = router.route(
        "BRAF mechanism",
        preference="deep_research",
        max_subqueries=2,
        max_followup_rounds=9,
    )

    assert identifier.question_type == "identifier"
    assert identifier.strategy == "direct"
    assert len(identifier.subqueries) == 1
    assert forced.strategy == "deep_research"
    assert len(forced.subqueries) <= 2
    assert forced.max_followup_rounds == 1


def test_gap_followups_are_deduplicated_and_limited() -> None:
    values = DeterministicResearchRouter.followup_queries(
        "BRAF prognosis",
        gaps=["缺少长期结局", "缺少亚组分析", "第三个缺口"],
        existing_queries=["BRAF prognosis"],
        limit=9,
    )

    assert len(values) == 2
    assert "长期结局" in values[0]
    assert len(set(values)) == 2


def test_multisubquery_cache_key_includes_routing_context() -> None:
    base = DeterministicQueryPlanner().plan("BRAF prognosis")
    first = replace(
        base,
        routing_context="decomposition",
        parent_query="BRAF comparison and prognosis",
        subquery_id="q1",
    )
    second = replace(first, subquery_id="q2")
    project_id = uuid4()
    config = retrieval_config()

    first_payload = retrieval_cache_key_payload(
        query_plan=first,
        project_id=project_id,
        collection_id=None,
        project_revision=1,
        limit=10,
        config=config,
    )
    second_payload = retrieval_cache_key_payload(
        query_plan=second,
        project_id=project_id,
        collection_id=None,
        project_revision=1,
        limit=10,
        config=config,
    )

    assert first_payload["routing_context"] == "decomposition"
    assert first_payload["parent_query"] == "BRAF comparison and prognosis"
    assert first_payload != second_payload


def test_decomposition_keeps_dense_route_only_for_primary_subquery() -> None:
    decision = DeterministicResearchRouter().route(
        "A versus B treatment comparison",
        max_subqueries=3,
    )
    primary = routed_query_plan(
        decision.subqueries[0].query,
        decision=decision,
        subquery_id=decision.subqueries[0].subquery_id,
        focus=decision.subqueries[0].focus,
        parent_query="A versus B treatment comparison",
    )
    secondary = routed_query_plan(
        decision.subqueries[1].query,
        decision=decision,
        subquery_id=decision.subqueries[1].subquery_id,
        focus=decision.subqueries[1].focus,
        parent_query="A versus B treatment comparison",
    )

    assert primary.dense_queries
    assert secondary.dense_queries == ()
    assert secondary.english_query is None
    assert secondary.expansions == ()
    assert decision.strategy == "decomposition"
    assert decision.subqueries[0].subquery_id == "q1"
    assert decision.subqueries[1].subquery_id != "q1"


@pytest.mark.asyncio
async def test_controlled_retriever_runs_subqueries_in_parallel_and_one_followup_round() -> None:
    router = DeterministicResearchRouter()
    decision = router.route(
        "A versus B treatment comparison",
        preference="deep_research",
        max_subqueries=3,
    )
    active = 0
    max_active = 0
    concurrent = asyncio.Event()

    async def retrieve(
        query: str,
        subquery_id: str,
        focus: str,
        round_number: int,
    ) -> ControlledRetrievalStep:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active >= 2:
            concurrent.set()
        await asyncio.wait_for(concurrent.wait(), timeout=1)
        chunk_id = uuid4()
        candidate = RetrievalCandidate(
            chunk_id=chunk_id,
            evidence_id=f"ev1.test.{chunk_id}.signature",
            text=query,
            score=float(len(query)),
            source_locator={"focus": focus},
            paper_id=uuid4(),
            role="anchor",
            anchor_chunk_id=chunk_id,
        )
        active -= 1
        return ControlledRetrievalStep(
            subquery_id=subquery_id,
            focus=focus,
            query=query,
            round=round_number,
            candidates=(candidate,),
            cache_level="miss",
        )

    execution = await ControlledResearchRetriever(retrieve).execute(
        decision,
        limit=5,
        followup_queries=("followup one", "followup two", "ignored third"),
    )

    assert max_active >= 2
    assert len([step for step in execution.steps if step.round == 0]) == 3
    assert len([step for step in execution.steps if step.round == 1]) == 2
    assert execution.followup_queries == ("followup one", "followup two")
    assert len(execution.candidates) == 5
