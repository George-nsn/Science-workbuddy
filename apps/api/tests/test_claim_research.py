import asyncio
from uuid import uuid4

import pytest

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.services.claim_research import ControlledClaimResearchCoordinator
from science_buddy.services.dynamic_research import ActionDecision, ProposedResearchAction


def decision(action_id: str, claim_key: str) -> ActionDecision:
    return ActionDecision(
        action=ProposedResearchAction(
            action_id=action_id,
            action_type="local_hybrid_search",
            query=f"query {action_id}",
            target_claim_ids=[claim_key],
        ),
        score=0.8,
        approved=True,
        rejection_reason=None,
    )


@pytest.mark.asyncio
async def test_claim_workers_are_bounded_and_merge_candidates_deterministically() -> None:
    active = 0
    max_active = 0
    shared_chunk = uuid4()

    async def execute(values, round_number):  # type: ignore[no-untyped-def]
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        value = values[0]
        candidate = RetrievalCandidate(
            chunk_id=shared_chunk,
            evidence_id="ev1.shared",
            text="shared evidence",
            score=0.5 + 0.1 * len(value.action.target_claim_ids[0]),
            source_locator={},
        )
        return [candidate], {value.action.action_id: {"round": round_number}}

    coordinator = ControlledClaimResearchCoordinator(
        execute=execute,
        max_workers=2,
        max_depth=2,
        parallel_safe=True,
    )
    candidates, results = await coordinator.execute_round(
        decisions=(
            decision("a1", "claim-a"),
            decision("b1", "claim-b"),
            decision("c1", "claim-c"),
        ),
        round_number=1,
    )

    assert 1 < max_active <= 2
    assert len(candidates) == 1
    assert set(results) == {"a1", "b1", "c1"}
    assert {value["claim_worker"] for value in results.values()} == {
        "claim-a",
        "claim-b",
        "claim-c",
    }
    too_deep = await coordinator.execute_round(
        decisions=(decision("a2", "claim-a"),),
        round_number=2,
        depth=3,
    )
    assert too_deep == ([], {})
