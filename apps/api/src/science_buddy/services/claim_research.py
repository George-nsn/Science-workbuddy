import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.services.dynamic_research import ActionDecision

ClaimActionExecutor = Callable[
    [tuple[ActionDecision, ...], int],
    Awaitable[tuple[list[RetrievalCandidate], dict[str, dict[str, Any]]]],
]


@dataclass(frozen=True, slots=True)
class ClaimWorkerResult:
    claim_key: str
    depth: int
    candidates: tuple[RetrievalCandidate, ...]
    action_results: dict[str, dict[str, Any]]


class ControlledClaimResearchCoordinator:
    """Bounded claim workers with deterministic Evidence-ID/chunk merging."""

    def __init__(
        self,
        *,
        execute: ClaimActionExecutor,
        max_workers: int = 2,
        max_depth: int = 2,
        max_actions_per_worker: int = 2,
        allowed_actions: frozenset[str] | None = None,
        parallel_safe: bool = True,
    ) -> None:
        self._execute = execute
        self._max_workers = max(1, min(max_workers, 4))
        self._max_depth = max(1, min(max_depth, 2))
        self._max_actions_per_worker = max(1, min(max_actions_per_worker, 4))
        self._allowed_actions = allowed_actions
        self._parallel_safe = parallel_safe

    async def execute_round(
        self,
        *,
        decisions: Sequence[ActionDecision],
        round_number: int,
        depth: int = 1,
    ) -> tuple[list[RetrievalCandidate], dict[str, dict[str, Any]]]:
        if depth > self._max_depth:
            return [], {}
        by_claim: dict[str, list[ActionDecision]] = {}
        unscoped: list[ActionDecision] = []
        for decision in decisions:
            if (
                self._allowed_actions is not None
                and decision.action.action_type not in self._allowed_actions
            ):
                continue
            if not decision.action.target_claim_ids:
                unscoped.append(decision)
                continue
            for claim_key in decision.action.target_claim_ids:
                by_claim.setdefault(claim_key, []).append(decision)
        groups = [
            (claim_key, tuple(self._deduplicate_actions(values)))
            for claim_key, values in sorted(by_claim.items())
        ]
        if unscoped:
            groups.append(("unscoped", tuple(self._deduplicate_actions(unscoped))))
        groups = [
            (claim_key, values[: self._max_actions_per_worker])
            for claim_key, values in groups
        ]
        semaphore = asyncio.Semaphore(self._max_workers if self._parallel_safe else 1)

        async def run_worker(
            claim_key: str,
            values: tuple[ActionDecision, ...],
        ) -> ClaimWorkerResult:
            async with semaphore:
                candidates, results = await self._execute(values, round_number)
            return ClaimWorkerResult(
                claim_key=claim_key,
                depth=depth,
                candidates=tuple(candidates),
                action_results=results,
            )

        worker_results = await asyncio.gather(
            *(run_worker(claim_key, values) for claim_key, values in groups)
        )
        candidates_by_chunk: dict[object, RetrievalCandidate] = {}
        action_results: dict[str, dict[str, Any]] = {}
        for worker in worker_results:
            for candidate in worker.candidates:
                existing = candidates_by_chunk.get(candidate.chunk_id)
                if existing is None or candidate.score > existing.score:
                    candidates_by_chunk[candidate.chunk_id] = candidate
            for action_id, result in worker.action_results.items():
                action_results.setdefault(
                    action_id,
                    {
                        **result,
                        "claim_worker": worker.claim_key,
                        "worker_depth": worker.depth,
                    },
                )
        return (
            sorted(
                candidates_by_chunk.values(),
                key=lambda value: value.score,
                reverse=True,
            ),
            action_results,
        )

    @staticmethod
    def _deduplicate_actions(values: Sequence[ActionDecision]) -> list[ActionDecision]:
        output: list[ActionDecision] = []
        seen: set[str] = set()
        for value in values:
            key = value.action.action_id
            if key in seen:
                continue
            seen.add(key)
            output.append(value)
        return output
