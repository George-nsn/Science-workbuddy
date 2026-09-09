from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import RetrievalRouteMemory
from science_buddy.services.research_routing import ControlledRetrievalStep

RouteStatus = Literal["empty", "failed", "rejected"]
_RETRYABLE_MARKERS = (
    "timeout",
    "timed out",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "network",
    "temporar",
    "rate limit",
    "database is locked",
)
_NON_RETRYABLE_MARKERS = (
    "not configured",
    "not_configured",
    "unauthorized",
    "forbidden",
    "401",
    "403",
    "unsupported",
    "invalid",
    "not allowed",
    "not_authorized",
    "rejected",
)


@dataclass(frozen=True, slots=True)
class RetryAssessment:
    retry_worthy: bool
    reason: str


def assess_retry(failure_reason: str, *, status: RouteStatus) -> RetryAssessment:
    normalized = failure_reason.casefold()
    if status == "rejected" or any(value in normalized for value in _NON_RETRYABLE_MARKERS):
        return RetryAssessment(False, "change_permissions_configuration_or_action")
    if any(value in normalized for value in _RETRYABLE_MARKERS):
        return RetryAssessment(True, "transient_failure_retry_with_backoff")
    if status == "empty":
        return RetryAssessment(False, "reformulate_query_or_change_scope")
    return RetryAssessment(False, "inspect_failure_before_retry")


def _query_hash(value: str) -> str:
    return hashlib.sha256(" ".join(value.casefold().split()).encode("utf-8")).hexdigest()


class RetrievalRouteMemoryService:
    """Persist failed, rejected, and empty retrieval routes as planning experience."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        project_id: UUID,
        workflow_id: UUID,
        workflow_kind: str,
        query: str,
        tool: str,
        scope: Mapping[str, Any],
        result_count: int,
        status: RouteStatus,
        failure_reason: str,
        round_number: int = 0,
        research_run_id: UUID | None = None,
        brainstorm_session_id: UUID | None = None,
        synthesis_session_id: UUID | None = None,
        route_metadata: Mapping[str, Any] | None = None,
    ) -> RetrievalRouteMemory:
        assessment = assess_retry(failure_reason, status=status)
        value = RetrievalRouteMemory(
            project_id=project_id,
            workflow_id=workflow_id,
            workflow_kind=workflow_kind[:32],
            research_run_id=research_run_id,
            brainstorm_session_id=brainstorm_session_id,
            synthesis_session_id=synthesis_session_id,
            round_number=round_number,
            query=query[:2000],
            query_hash=_query_hash(query),
            tool=tool[:64],
            scope=dict(scope),
            result_count=max(0, result_count),
            status=status,
            failure_reason=failure_reason[:2000],
            retry_worthy=assessment.retry_worthy,
            retry_reason=assessment.reason,
            route_metadata=dict(route_metadata or {}),
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def record_steps(
        self,
        *,
        project_id: UUID,
        workflow_id: UUID,
        workflow_kind: str,
        steps: Sequence[ControlledRetrievalStep],
        scope: Mapping[str, Any],
        research_run_id: UUID | None = None,
        brainstorm_session_id: UUID | None = None,
        synthesis_session_id: UUID | None = None,
    ) -> list[RetrievalRouteMemory]:
        output: list[RetrievalRouteMemory] = []
        for step in steps:
            routes = (
                step.report.get("routes", [])
                if isinstance(step.report, Mapping)
                else []
            )
            recorded_route = False
            for raw_route in routes:
                if not isinstance(raw_route, Mapping):
                    continue
                result_count = int(raw_route.get("candidates") or 0)
                error = str(raw_route.get("error") or "").strip()
                if not error and result_count > 0:
                    continue
                recorded_route = True
                status: RouteStatus = "failed" if error else "empty"
                output.append(
                    await self.record(
                        project_id=project_id,
                        workflow_id=workflow_id,
                        workflow_kind=workflow_kind,
                        research_run_id=research_run_id,
                        brainstorm_session_id=brainstorm_session_id,
                        synthesis_session_id=synthesis_session_id,
                        round_number=step.round,
                        query=step.query,
                        tool=str(raw_route.get("route") or step.focus),
                        scope=scope,
                        result_count=result_count,
                        status=status,
                        failure_reason=error or "no_candidates",
                        route_metadata={
                            key: value
                            for key, value in raw_route.items()
                            if key not in {"error", "candidates"}
                        },
                    )
                )
            if not recorded_route and not step.candidates:
                reason = "; ".join(step.route_errors) or "no_candidates"
                output.append(
                    await self.record(
                        project_id=project_id,
                        workflow_id=workflow_id,
                        workflow_kind=workflow_kind,
                        research_run_id=research_run_id,
                        brainstorm_session_id=brainstorm_session_id,
                        synthesis_session_id=synthesis_session_id,
                        round_number=step.round,
                        query=step.query,
                        tool=step.focus,
                        scope=scope,
                        result_count=0,
                        status="failed" if step.route_errors else "empty",
                        failure_reason=reason,
                        route_metadata={"cache_level": step.cache_level},
                    )
                )
        return output

    async def record_action_results(
        self,
        *,
        project_id: UUID,
        workflow_id: UUID,
        workflow_kind: str,
        round_number: int,
        actions: Sequence[Mapping[str, Any]],
        scope: Mapping[str, Any],
        research_run_id: UUID | None = None,
    ) -> list[RetrievalRouteMemory]:
        output: list[RetrievalRouteMemory] = []
        for action in actions:
            error = str(action.get("error") or "").strip()
            executed = bool(action.get("executed", False))
            result_count = int(action.get("results") or 0)
            if executed and not error and result_count > 0:
                continue
            status: RouteStatus = (
                "rejected" if not executed else "failed" if error else "empty"
            )
            queries = action.get("queries")
            query = " ".join(str(value) for value in queries) if isinstance(queries, list) else ""
            output.append(
                await self.record(
                    project_id=project_id,
                    workflow_id=workflow_id,
                    workflow_kind=workflow_kind,
                    research_run_id=research_run_id,
                    round_number=round_number,
                    query=query,
                    tool=str(action.get("tool") or "unknown"),
                    scope=scope,
                    result_count=result_count,
                    status=status,
                    failure_reason=(
                        error
                        or str(action.get("reason") or "no_candidates")
                    ),
                    route_metadata=dict(action),
                )
            )
        return output
