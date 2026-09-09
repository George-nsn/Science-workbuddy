from uuid import uuid4

import pytest

from science_buddy.domain.contracts import ClaimDraft, RetrievalCandidate
from science_buddy.infrastructure.models import Chunk
from science_buddy.services.evidence import EvidenceTokenService, MechanicalEvidenceVerifier
from science_buddy.services.retrieval import (
    RankedChunk,
    RouteResult,
    reciprocal_rank_fusion,
    weighted_reciprocal_rank_fusion,
)


def test_evidence_token_is_bound_to_workflow_and_chunk() -> None:
    service = EvidenceTokenService("a-test-secret-that-is-long-enough")
    workflow_id = uuid4()
    chunk_id = uuid4()
    evidence_id = service.issue(workflow_id, chunk_id)

    assert service.verify(evidence_id, workflow_id=workflow_id, chunk_id=chunk_id)
    assert not service.verify(evidence_id, workflow_id=uuid4(), chunk_id=chunk_id)


@pytest.mark.asyncio
async def test_mechanical_verifier_rejects_unknown_evidence() -> None:
    workflow_id = uuid4()
    chunk_id = uuid4()
    tokens = EvidenceTokenService("a-test-secret-that-is-long-enough")
    evidence_id = tokens.issue(workflow_id, chunk_id)
    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        evidence_id=evidence_id,
        text="Stored source evidence.",
        score=1.0,
        source_locator={"pmid": "123"},
    )
    verifier = MechanicalEvidenceVerifier(tokens, workflow_id=workflow_id)

    results = await verifier.verify(
        [
            ClaimDraft("Supported claim", (evidence_id,)),
            ClaimDraft("Unsupported claim", ("invented",)),
        ],
        [candidate],
    )

    assert results == [True, False]


def test_rrf_rewards_items_present_in_multiple_rankings() -> None:
    shared, dense_only, sparse_only = uuid4(), uuid4(), uuid4()
    fused = reciprocal_rank_fusion([[dense_only, shared], [sparse_only, shared]])

    assert fused[0][0] == shared


def test_weighted_rrf_preserves_route_contributions() -> None:
    shared_id, lexical_id = uuid4(), uuid4()
    paper_id = uuid4()
    shared = Chunk(
        id=shared_id,
        section_id=uuid4(),
        ordinal=0,
        text="shared",
        content_hash="shared-hash",
        char_start=0,
        char_end=6,
        source_locator={},
    )
    lexical = Chunk(
        id=lexical_id,
        section_id=uuid4(),
        ordinal=0,
        text="lexical",
        content_hash="lexical-hash",
        char_start=0,
        char_end=7,
        source_locator={},
    )
    routes = [
        RouteResult(
            "dense_original",
            (RankedChunk(shared, paper_id, 0.8),),
            10,
        ),
        RouteResult(
            "fts_english",
            (
                RankedChunk(lexical, paper_id, 0.9),
                RankedChunk(shared, paper_id, 0.7),
            ),
            5,
        ),
    ]

    fused = weighted_reciprocal_rank_fusion(
        routes,
        weights={"dense_original": 1.0, "fts_english": 0.9},
        k=60,
    )

    assert fused[0].chunk_id == shared_id
    assert {trace.route for trace in fused[0].traces} == {
        "dense_original",
        "fts_english",
    }
