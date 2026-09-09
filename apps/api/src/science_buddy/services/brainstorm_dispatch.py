import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    BrainstormMessage,
    BrainstormSession,
    WorkbenchTask,
)

_HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_MARKDOWN_DECORATION = re.compile(r"[`*_~\[\]]")
_SOURCE_KIND = "brainstorm_message"
_MAX_TASKS = 30


@dataclass(frozen=True, slots=True)
class TaskDraft:
    title: str
    source_section: str
    source_key: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    session_id: UUID
    message_id: UUID
    sections_detected: int
    created_count: int
    tasks: list[WorkbenchTask]

    @property
    def skipped_existing(self) -> int:
        return self.sections_detected - self.created_count


def _clean_title(value: object, fallback: str) -> str:
    text = _MARKDOWN_DECORATION.sub("", str(value or ""))
    text = re.sub(r"^\s*(?:\d+[.)、]|[-+])\s*", "", text)
    cleaned = " ".join(text.split()).strip("：:。.- ")
    return (cleaned or fallback)[:300]


def _source_key(origin: str, ordinal: int, title: str) -> str:
    value = f"v1:{origin}:{ordinal}:{title.casefold()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _structured_work_packages(payload: dict[str, object]) -> list[TaskDraft]:
    experiment = payload.get("experiment_agent")
    if not isinstance(experiment, dict):
        return []
    packages = experiment.get("work_packages")
    if not isinstance(packages, list):
        return []
    drafts: list[TaskDraft] = []
    for ordinal, package in enumerate(packages, start=1):
        if not isinstance(package, dict):
            continue
        title = _clean_title(package.get("title"), f"实验工作包 {ordinal}")
        drafts.append(
            TaskDraft(
                title=title,
                source_section=f"实验设计 > {title}"[:500],
                source_key=_source_key("work_package", ordinal, title),
            )
        )
    return drafts[:_MAX_TASKS]


def _markdown_sections(content: str) -> list[TaskDraft]:
    matches = list(_HEADING_PATTERN.finditer(content))
    nested_matches = [match for match in matches if len(match.group(1)) >= 2]
    selected_matches = nested_matches or matches
    stack: dict[int, str] = {}
    drafts: list[TaskDraft] = []
    for ordinal, match in enumerate(selected_matches, start=1):
        level = len(match.group(1))
        title = _clean_title(match.group(2), f"实验章节 {ordinal}")
        stack[level] = title
        for deeper_level in range(level + 1, 5):
            stack.pop(deeper_level, None)
        section_path = " > ".join(stack[item] for item in sorted(stack) if item <= level)
        drafts.append(
            TaskDraft(
                title=title,
                source_section=section_path[:500],
                source_key=_source_key("markdown_heading", ordinal, title),
            )
        )
        if len(drafts) == _MAX_TASKS:
            break
    return drafts


def extract_experimental_tasks(message: BrainstormMessage) -> list[TaskDraft]:
    structured = _structured_work_packages(message.payload)
    if structured:
        return structured
    sections = _markdown_sections(message.content)
    if sections:
        return sections
    title = _clean_title(message.content.splitlines()[0] if message.content else "", "实验方案")
    return [
        TaskDraft(
            title=title,
            source_section="完整协调输出",
            source_key=_source_key("whole_message", 1, title),
        )
    ]


class BrainstormTaskDispatchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def dispatch(
        self,
        *,
        session_id: UUID,
        message_id: UUID,
        work_date: date | None,
        priority: Literal["low", "medium", "high"],
    ) -> DispatchResult:
        brainstorm = await self._session.get(BrainstormSession, session_id)
        if brainstorm is None or brainstorm.deleted_at is not None:
            raise LookupError("Brainstorm session does not exist")
        message = await self._session.get(BrainstormMessage, message_id)
        if message is None or message.session_id != session_id:
            raise LookupError("Brainstorm message does not exist in this session")
        if message.role != "assistant":
            raise ValueError("Only assistant experiment plans can be sent to the workbench")

        drafts = extract_experimental_tasks(message)
        source_keys = [draft.source_key for draft in drafts]
        existing_keys = set(
            (
                await self._session.scalars(
                    select(WorkbenchTask.source_key).where(
                        WorkbenchTask.project_id == brainstorm.project_id,
                        WorkbenchTask.source_kind == _SOURCE_KIND,
                        WorkbenchTask.source_id == message.id,
                        WorkbenchTask.source_key.in_(source_keys),
                        WorkbenchTask.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        new_drafts = [draft for draft in drafts if draft.source_key not in existing_keys]
        if new_drafts:
            await self._session.execute(
                sqlite_insert(WorkbenchTask)
                .values(
                    [
                        {
                            "project_id": brainstorm.project_id,
                            "work_date": work_date,
                            "title": draft.title,
                            "status": "todo",
                            "priority": priority,
                            "source_kind": _SOURCE_KIND,
                            "source_id": message.id,
                            "source_key": draft.source_key,
                            "source_section": draft.source_section,
                        }
                        for draft in new_drafts
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        WorkbenchTask.project_id,
                        WorkbenchTask.source_kind,
                        WorkbenchTask.source_id,
                        WorkbenchTask.source_key,
                    ]
                )
            )
            await self._session.flush()

        rows = list(
            (
                await self._session.scalars(
                    select(WorkbenchTask).where(
                        WorkbenchTask.project_id == brainstorm.project_id,
                        WorkbenchTask.source_kind == _SOURCE_KIND,
                        WorkbenchTask.source_id == message.id,
                        WorkbenchTask.source_key.in_(source_keys),
                        WorkbenchTask.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        by_key = {cast(str, task.source_key): task for task in rows}
        ordered = [by_key[key] for key in source_keys if key in by_key]
        return DispatchResult(
            session_id=session_id,
            message_id=message_id,
            sections_detected=len(drafts),
            created_count=len([draft for draft in new_drafts if draft.source_key in by_key]),
            tasks=ordered,
        )