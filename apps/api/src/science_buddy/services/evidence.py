import base64
import hashlib
import hmac
from collections.abc import Sequence
from uuid import UUID

from science_buddy.domain.contracts import ClaimDraft, RetrievalCandidate


class EvidenceTokenService:
    """Issue tamper-evident IDs that bind a workflow to a stored chunk."""

    _VERSION = "ev1"

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("Evidence signing key must contain at least 16 characters")
        self._secret = secret.encode("utf-8")

    def issue(self, workflow_id: UUID, chunk_id: UUID) -> str:
        payload = f"{workflow_id.hex}.{chunk_id.hex}"
        signature = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()[:16]
        encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"{self._VERSION}.{payload}.{encoded}"

    def verify(self, evidence_id: str, *, workflow_id: UUID, chunk_id: UUID) -> bool:
        expected = self.issue(workflow_id, chunk_id)
        return hmac.compare_digest(expected, evidence_id)


class MechanicalEvidenceVerifier:
    """Validate evidence membership and tokens without claiming semantic entailment."""

    def __init__(self, token_service: EvidenceTokenService, *, workflow_id: UUID) -> None:
        self._tokens = token_service
        self._workflow_id = workflow_id

    async def verify(
        self,
        claims: Sequence[ClaimDraft],
        candidates: Sequence[RetrievalCandidate],
    ) -> list[bool]:
        by_id = {candidate.evidence_id: candidate for candidate in candidates}
        results: list[bool] = []
        for claim in claims:
            if not claim.statement.strip() or not claim.evidence_ids:
                results.append(False)
                continue
            valid = True
            for evidence_id in claim.evidence_ids:
                candidate = by_id.get(evidence_id)
                if candidate is None or not self._tokens.verify(
                    evidence_id,
                    workflow_id=self._workflow_id,
                    chunk_id=candidate.chunk_id,
                ):
                    valid = False
                    break
            results.append(valid)
        return results
