from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

import pytest

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.providers import StructuredGenerationRequest
from science_buddy.services.evidence import EvidenceTokenService, MechanicalEvidenceVerifier
from science_buddy.services.research import ResearchPipeline


class SequentialModel:
    name = "test"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        return self.responses.pop(0)

    async def stream_text(
        self, system: str, messages: Sequence[str]
    ) -> AsyncIterator[str]:
        yield ""


@pytest.mark.asyncio
async def test_research_pipeline_keeps_only_verified_claims() -> None:
    workflow_id = uuid4()
    chunk_id = uuid4()
    tokens = EvidenceTokenService("a-test-secret-that-is-long-enough")
    evidence_id = tokens.issue(workflow_id, chunk_id)
    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        evidence_id=evidence_id,
        text="BRAF V600E was associated with the measured outcome in this cohort.",
        score=1.0,
        source_locator={"pmid": "123", "section_path": "Results"},
    )
    model = SequentialModel(
        [
            {
                "claims": [
                    {
                        "statement": "The cohort showed an association.",
                        "evidence_ids": [evidence_id],
                        "relation": "supports",
                    },
                    {
                        "statement": "Invented claim.",
                        "evidence_ids": ["invented"],
                        "relation": "supports",
                    },
                ],
                "gaps": ["No causal estimate."],
                "conflicts": [],
                "followup_queries": ["BRAF V600E causal perturbation study"],
            },
            {
                "verdicts": [
                    {
                        "claim_index": 0,
                        "label": "entailment",
                        "confidence": 0.95,
                        "reason": "Directly stated.",
                        "quantitative_consistency_check": "not_applicable",
                        "temporal_scope_check": "not_applicable",
                        "study_subject_scope_check": "pass",
                        "effect_direction_check": "pass",
                        "model_name": "test-nli",
                        "verifier_version": "claim-nli-v1",
                    }
                ]
            },
            {
                "answer": f"The cohort showed an association. [{evidence_id}]",
                "used_claim_indexes": [0],
            },
        ]
    )
    pipeline = ResearchPipeline(
        model=model,
        mechanical_verifier=MechanicalEvidenceVerifier(tokens, workflow_id=workflow_id),
    )

    result = await pipeline.answer(
        question="What was observed?",
        language="en",
        candidates=[candidate],
    )

    assert len(result.claims) == 1
    assert result.claims[0].evidence[0].source_locator["pmid"] == "123"
    assert result.gaps == ["No causal estimate."]
    assert result.followup_queries == ["BRAF V600E causal perturbation study"]
    assert result.claims[0].verification_label == "entailment"
    assert result.claims[0].verification_confidence == 0.95
    assert result.claims[0].verification_model == "test-nli"


@pytest.mark.asyncio
async def test_research_pipeline_downgrades_low_confidence_claim_to_gap() -> None:
    workflow_id = uuid4()
    chunk_id = uuid4()
    tokens = EvidenceTokenService("a-test-secret-that-is-long-enough")
    evidence_id = tokens.issue(workflow_id, chunk_id)
    candidate = RetrievalCandidate(
        chunk_id=chunk_id,
        evidence_id=evidence_id,
        text="An association was reported in a small exploratory subgroup.",
        score=1.0,
        source_locator={},
    )
    model = SequentialModel(
        [
            {
                "claims": [
                    {
                        "statement": "The association is established in all patients.",
                        "evidence_ids": [evidence_id],
                        "relation": "supports",
                    }
                ],
                "gaps": [],
                "conflicts": [],
                "followup_queries": [],
            },
            {
                "verdicts": [
                    {
                        "claim_index": 0,
                        "label": "insufficient",
                        "confidence": 0.55,
                        "reason": "Population extrapolation.",
                        "study_subject_scope_check": "fail",
                    }
                ]
            },
        ]
    )
    result = await ResearchPipeline(
        model=model,
        mechanical_verifier=MechanicalEvidenceVerifier(tokens, workflow_id=workflow_id),
    ).answer(question="Is it established?", language="en", candidates=[candidate])

    assert result.claims == []
    assert any("低置信度或不充分" in value for value in result.gaps)


def test_semantic_verdict_accepts_legacy_ambiguous_field_names() -> None:
    from science_buddy.services.research import FinalClaim, SemanticVerdict

    verdict = SemanticVerdict.model_validate(
        {
            "claim_index": 0,
            "label": "entailment",
            "confidence": 0.9,
            "reason": "Legacy response",
            "numeric_check": "pass",
            "temporal_check": "not_applicable",
            "population_check": "pass",
            "directionality_check": "pass",
        }
    )

    assert verdict.quantitative_consistency_check == "pass"
    assert verdict.temporal_scope_check == "not_applicable"
    assert verdict.study_subject_scope_check == "pass"
    assert verdict.effect_direction_check == "pass"
    claim = FinalClaim(
        statement="Legacy claim",
        relation="supports",
        evidence=[],
        semantic_verification="Legacy audit",
        verification_checks={
            "numeric": "pass",
            "population": "not_applicable",
        },
    )
    assert claim.verification_checks == {
        "quantitative_consistency": "pass",
        "study_subject_scope": "not_applicable",
    }
