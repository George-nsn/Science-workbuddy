import asyncio
import logging
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from science_buddy.domain.enums import JobStatus
from science_buddy.infrastructure.models import BrainstormSession, Job, SynthesisSession

logger = logging.getLogger(__name__)

_BRAINSTORM_JOB_KINDS = {
    "brainstorm_plan_discovery": "plan_directions",
    "brainstorm_plan_generation": "ready_to_generate",
}
_SYNTHESIS_JOB_KIND = "synthesis_manuscript"


async def recover_interrupted_background_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Fail request-independent jobs left active by an API process restart."""
    async with session_factory() as session:
        jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.kind.in_([*_BRAINSTORM_JOB_KINDS, _SYNTHESIS_JOB_KIND]),
                        Job.status.in_(
                            [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING]
                        ),
                    )
                )
            ).all()
        )
        for job in jobs:
            job.status = JobStatus.FAILED
            job.error_message = (
                "API 服务在后台任务完成前重启；任务未自动重放，请重新提交。"
            )
            session_id = job.payload.get("session_id")
            if not session_id:
                continue
            brainstorm = await session.get(BrainstormSession, UUID(str(session_id)))
            if job.kind == _SYNTHESIS_JOB_KIND:
                synthesis = await session.get(SynthesisSession, UUID(str(session_id)))
                if synthesis is not None and synthesis.deleted_at is None:
                    synthesis.status = "failed"
                    synthesis.error_message = job.error_message
                continue
            if brainstorm is not None and brainstorm.deleted_at is None:
                brainstorm.status = "active"
                brainstorm.phase = _BRAINSTORM_JOB_KINDS[job.kind]
                snapshot = dict(brainstorm.plan_snapshot)
                snapshot["background_job"] = {
                    "id": str(job.id),
                    "kind": job.kind.removeprefix("brainstorm_"),
                    "status": job.status.value,
                    "error_message": job.error_message,
                }
                brainstorm.plan_snapshot = snapshot
        if jobs:
            await session.commit()
        return len(jobs)


class InProcessTaskManager:
    """Keep request-independent tasks alive for the lifetime of the API process."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    def start(self, coroutine: Coroutine[Any, Any, None]) -> None:
        if self._closing:
            coroutine.close()
            raise RuntimeError("Background task manager is shutting down")
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.exception(
                "background_task_failed error_type=%s",
                type(error).__name__,
                exc_info=error,
            )

    async def shutdown(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
