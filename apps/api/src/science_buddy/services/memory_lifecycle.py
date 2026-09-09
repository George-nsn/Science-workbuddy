from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from science_buddy.infrastructure.models import (
    BrainstormSession,
    CacheEntry,
    MemoryDerivedIndex,
    Project,
    ProjectFact,
    ProjectFactRelation,
    ResearchActionAudit,
    ResearchClaimTarget,
    ResearchPlanSnapshot,
    ResearchRound,
    ResearchRun,
    ResearchStepMemory,
    SufficiencyAssessment,
    WorkbenchAttachment,
    WorkbenchNote,
    WorkbenchTask,
)

TrashEntityType = Literal[
    "brainstorm_session",
    "research_run",
    "workbench_note",
    "workbench_task",
    "workbench_attachment",
]


@dataclass(frozen=True, slots=True)
class TrashRecord:
    entity_type: TrashEntityType
    entity_id: UUID
    title: str
    deleted_at: datetime
    purge_after: datetime


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    cache_entries_deleted: int
    trash_items_purged: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MemoryLifecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def set_retention(self, project_id: UUID, days: int) -> Project:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        if not 1 <= days <= 3650:
            raise ValueError("Retention must be between 1 and 3650 days")
        project.trash_retention_days = days
        await self._session.commit()
        await self._session.refresh(project)
        return project

    async def list_trash(self, project_id: UUID) -> list[TrashRecord]:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        records: list[TrashRecord] = []
        sessions = (
            await self._session.scalars(
                select(BrainstormSession).where(
                    BrainstormSession.project_id == project_id,
                    BrainstormSession.deleted_at.is_not(None),
                )
            )
        ).all()
        for brainstorm_session in sessions:
            records.append(
                self._record(
                    "brainstorm_session",
                    brainstorm_session.id,
                    brainstorm_session.title,
                    brainstorm_session.deleted_at,
                    project,
                )
            )
        research_runs = (
            await self._session.scalars(
                select(ResearchRun).where(
                    ResearchRun.project_id == project_id,
                    ResearchRun.deleted_at.is_not(None),
                )
            )
        ).all()
        for research_run in research_runs:
            records.append(
                self._record(
                    "research_run",
                    research_run.id,
                    research_run.question,
                    research_run.deleted_at,
                    project,
                )
            )
        notes = (
            await self._session.scalars(
                select(WorkbenchNote).where(
                    WorkbenchNote.project_id == project_id,
                    WorkbenchNote.deleted_at.is_not(None),
                )
            )
        ).all()
        for note in notes:
            records.append(
                self._record(
                    "workbench_note",
                    note.id,
                    note.title,
                    note.deleted_at,
                    project,
                )
            )
        tasks = (
            await self._session.scalars(
                select(WorkbenchTask).where(
                    WorkbenchTask.project_id == project_id,
                    WorkbenchTask.deleted_at.is_not(None),
                )
            )
        ).all()
        for task in tasks:
            records.append(
                self._record(
                    "workbench_task",
                    task.id,
                    task.title,
                    task.deleted_at,
                    project,
                )
            )
        attachments = (
            await self._session.scalars(
                select(WorkbenchAttachment).where(
                    WorkbenchAttachment.project_id == project_id,
                    WorkbenchAttachment.deleted_at.is_not(None),
                )
            )
        ).all()
        for attachment in attachments:
            records.append(
                self._record(
                    "workbench_attachment",
                    attachment.id,
                    attachment.filename,
                    attachment.deleted_at,
                    project,
                )
            )
        return sorted(records, key=lambda item: item.deleted_at, reverse=True)

    async def restore(
        self,
        *,
        project_id: UUID,
        entity_type: TrashEntityType,
        entity_id: UUID,
    ) -> None:
        value = await self._resolve(project_id, entity_type, entity_id)
        if value.deleted_at is None:
            raise ValueError("Item is not in the recycle bin")
        value.deleted_at = None
        value.delete_reason = None
        if isinstance(value, WorkbenchNote):
            attachments = (
                await self._session.scalars(
                    select(WorkbenchAttachment).where(
                        WorkbenchAttachment.note_id == value.id,
                        WorkbenchAttachment.deleted_at.is_not(None),
                    )
                )
            ).all()
            for attachment in attachments:
                attachment.deleted_at = None
                attachment.delete_reason = None
        elif isinstance(value, BrainstormSession):
            facts = (
                await self._session.scalars(
                    select(ProjectFact).where(
                        ProjectFact.source_session_id == value.id,
                        ProjectFact.deleted_at.is_not(None),
                    )
                )
            ).all()
            for fact in facts:
                fact.deleted_at = None
        await self._session.commit()

    async def purge(
        self,
        *,
        project_id: UUID,
        entity_type: TrashEntityType,
        entity_id: UUID,
    ) -> None:
        value = await self._resolve(project_id, entity_type, entity_id)
        if value.deleted_at is None:
            raise ValueError("Only recycle-bin items can be permanently deleted")
        paths: list[Path] = []
        if isinstance(value, WorkbenchNote):
            attachments = (
                await self._session.scalars(
                    select(WorkbenchAttachment).where(WorkbenchAttachment.note_id == value.id)
                )
            ).all()
            paths.extend(Path(item.storage_path) for item in attachments)
            for attachment in attachments:
                await self._session.delete(attachment)
        elif isinstance(value, WorkbenchAttachment):
            paths.append(Path(value.storage_path))
        elif isinstance(value, ResearchRun):
            step_ids = select(ResearchStepMemory.id).where(
                ResearchStepMemory.research_run_id == value.id
            )
            await self._session.execute(
                delete(MemoryDerivedIndex).where(
                    MemoryDerivedIndex.entity_type == "research_step",
                    MemoryDerivedIndex.entity_id.in_(step_ids),
                )
            )
            await self._session.execute(
                delete(ResearchStepMemory).where(
                    ResearchStepMemory.research_run_id == value.id
                )
            )
            await self._session.execute(
                delete(SufficiencyAssessment).where(
                    SufficiencyAssessment.research_run_id == value.id
                )
            )
            await self._session.execute(
                delete(ResearchClaimTarget).where(
                    ResearchClaimTarget.research_run_id == value.id
                )
            )
            await self._session.execute(
                delete(ResearchActionAudit).where(
                    ResearchActionAudit.research_run_id == value.id
                )
            )
            await self._session.execute(
                delete(ResearchRound).where(ResearchRound.research_run_id == value.id)
            )
            await self._session.execute(
                delete(ResearchPlanSnapshot).where(
                    ResearchPlanSnapshot.research_run_id == value.id
                )
            )
            fact_ids = select(ProjectFact.id).where(
                ProjectFact.source_type == "research_run",
                ProjectFact.source_id == value.id,
            )
            await self._session.execute(
                delete(ProjectFactRelation).where(
                    
                        ProjectFactRelation.source_fact_id.in_(fact_ids)
                        | ProjectFactRelation.target_fact_id.in_(fact_ids)
                    
                )
            )
            await self._session.execute(
                delete(MemoryDerivedIndex).where(
                    MemoryDerivedIndex.entity_type == "project_fact",
                    MemoryDerivedIndex.entity_id.in_(fact_ids),
                )
            )
            await self._session.execute(
                delete(ProjectFact).where(
                    ProjectFact.source_type == "research_run",
                    ProjectFact.source_id == value.id,
                )
            )
        await self._session.delete(value)
        await self._session.commit()
        for path in paths:
            path.unlink(missing_ok=True)

    async def purge_expired_trash(self, now: datetime | None = None) -> int:
        current = as_utc(now or utc_now())
        projects = list((await self._session.scalars(select(Project))).all())
        purged = 0
        for project in projects:
            threshold = current - timedelta(days=project.trash_retention_days)
            records = await self.list_trash(project.id)
            for record in records:
                if as_utc(record.deleted_at) > threshold:
                    continue
                await self.purge(
                    project_id=project.id,
                    entity_type=record.entity_type,
                    entity_id=record.entity_id,
                )
                purged += 1
        return purged

    async def _resolve(
        self,
        project_id: UUID,
        entity_type: TrashEntityType,
        entity_id: UUID,
    ) -> BrainstormSession | ResearchRun | WorkbenchNote | WorkbenchTask | WorkbenchAttachment:
        if entity_type == "brainstorm_session":
            brainstorm = await self._session.get(BrainstormSession, entity_id)
            if brainstorm is None or brainstorm.project_id != project_id:
                raise LookupError("Recycle-bin item not found")
            return brainstorm
        if entity_type == "research_run":
            research = await self._session.get(ResearchRun, entity_id)
            if research is None or research.project_id != project_id:
                raise LookupError("Recycle-bin item not found")
            return research
        if entity_type == "workbench_note":
            note = await self._session.get(WorkbenchNote, entity_id)
            if note is None or note.project_id != project_id:
                raise LookupError("Recycle-bin item not found")
            return note
        if entity_type == "workbench_task":
            task = await self._session.get(WorkbenchTask, entity_id)
            if task is None or task.project_id != project_id:
                raise LookupError("Recycle-bin item not found")
            return task
        attachment = await self._session.get(WorkbenchAttachment, entity_id)
        if attachment is None or attachment.project_id != project_id:
            raise LookupError("Recycle-bin item not found")
        return attachment

    @staticmethod
    def _record(
        entity_type: TrashEntityType,
        entity_id: UUID,
        title: str,
        deleted_at: datetime | None,
        project: Project,
    ) -> TrashRecord:
        if deleted_at is None:
            raise ValueError("Trash record requires deleted_at")
        deleted = as_utc(deleted_at)
        return TrashRecord(
            entity_type=entity_type,
            entity_id=entity_id,
            title=title,
            deleted_at=deleted,
            purge_after=deleted + timedelta(days=project.trash_retention_days),
        )


async def cleanup_expired_cache_entries(
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            delete(CacheEntry).where(CacheEntry.expires_at <= (now or utc_now()))
        )
        await session.commit()
        return int(result.rowcount or 0)


async def run_memory_maintenance(
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
) -> MaintenanceResult:
    cache_deleted = await cleanup_expired_cache_entries(session_factory, now)
    async with session_factory() as session:
        trash_purged = await MemoryLifecycleService(session).purge_expired_trash(now)
    return MaintenanceResult(
        cache_entries_deleted=cache_deleted,
        trash_items_purged=trash_purged,
    )
