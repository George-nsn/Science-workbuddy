import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    BrainstormMessage,
    BrainstormSession,
    BrainstormVersion,
    Project,
    ProjectFact,
    RagCollection,
)


@dataclass(frozen=True, slots=True)
class SessionHistory:
    session: BrainstormSession
    messages: list[BrainstormMessage]
    versions: list[BrainstormVersion]


class BrainstormSessionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: UUID,
        mode: str,
        title: str | None,
        collection_id: UUID | None,
        allow_pubmed_search: bool,
        model_processing_allowed: bool,
        workflow: str = "classic",
        allow_web_search: bool = False,
        model_depth: str = "max",
        max_context_tokens: int = 1000000,
        agent_background: str = "",
    ) -> BrainstormSession:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError("Project does not exist")
        if mode not in {"exploration", "refinement"}:
            raise ValueError("Unsupported brainstorm mode")
        if workflow not in {"classic", "plan"}:
            raise ValueError("Unsupported brainstorm workflow")
        if workflow == "plan" and mode != "exploration":
            raise ValueError("Plan workflow is only available for exploration sessions")
        if model_depth not in {"quick", "balanced", "deep", "max"}:
            raise ValueError("Unsupported model depth")
        if max_context_tokens not in {
            32768,
            65536,
            131072,
            262144,
            524288,
            1000000,
        }:
            raise ValueError("Unsupported maximum context length")
        if collection_id:
            collection = await self._session.get(RagCollection, collection_id)
            if collection is None or collection.project_id != project_id:
                raise ValueError("RAG collection does not belong to the project")
        next_number = int(
            (
                await self._session.scalar(
                    select(func.coalesce(func.max(BrainstormSession.session_number), 0)).where(
                        BrainstormSession.project_id == project_id
                    )
                )
            )
            or 0
        ) + 1
        base_title = " ".join((title or "").split())[:200]
        if not base_title:
            base_title = (
                f"课题探索 · 会话{next_number}"
                if mode == "exploration"
                else f"课题完善 · 会话{next_number}"
            )
        for attempt in range(5):
            candidate = BrainstormSession(
                project_id=project_id,
                collection_id=collection_id,
                title=base_title,
                mode=mode,
                status="active",
                session_number=next_number + attempt,
                confirmation_round=0,
                allow_pubmed_search=allow_pubmed_search,
                model_processing_allowed=model_processing_allowed,
                workflow=workflow,
                phase="plan_directions" if workflow == "plan" else "conversation",
                allow_web_search=allow_web_search,
                plan_snapshot={},
                model_depth=model_depth,
                max_context_tokens=max_context_tokens,
                agent_background=agent_background.strip()[:6000],
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(candidate)
                    await self._session.flush()
                await self._session.commit()
                return candidate
            except IntegrityError:
                continue
        raise ValueError("Could not allocate a brainstorm session number")

    async def list_for_project(self, project_id: UUID) -> list[BrainstormSession]:
        return list(
            (
                await self._session.scalars(
                    select(BrainstormSession)
                    .where(
                        BrainstormSession.project_id == project_id,
                        BrainstormSession.deleted_at.is_(None),
                    )
                    .order_by(BrainstormSession.session_number.desc())
                )
            ).all()
        )

    async def rename(self, session_id: UUID, title: str) -> BrainstormSession:
        session = await self._session.get(BrainstormSession, session_id)
        if session is None or session.deleted_at is not None:
            raise ValueError("Brainstorm session does not exist")
        cleaned = " ".join(title.split())[:200]
        if not cleaned:
            raise ValueError("Session title must not be blank")
        session.title = cleaned
        await self._session.commit()
        return session

    async def history(self, session_id: UUID) -> SessionHistory:
        session = await self._session.get(BrainstormSession, session_id)
        if session is None or session.deleted_at is not None:
            raise ValueError("Brainstorm session does not exist")
        messages = list(
            (
                await self._session.scalars(
                    select(BrainstormMessage)
                    .where(BrainstormMessage.session_id == session_id)
                    .order_by(BrainstormMessage.sequence_number)
                )
            ).all()
        )
        versions = list(
            (
                await self._session.scalars(
                    select(BrainstormVersion)
                    .where(BrainstormVersion.session_id == session_id)
                    .order_by(BrainstormVersion.version_number)
                )
            ).all()
        )
        return SessionHistory(session=session, messages=messages, versions=versions)

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: str,
        content: str,
        agent_name: str | None = None,
        payload: dict[str, object] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> BrainstormMessage:
        sequence = int(
            (
                await self._session.scalar(
                    select(func.coalesce(func.max(BrainstormMessage.sequence_number), 0)).where(
                        BrainstormMessage.session_id == session_id
                    )
                )
            )
            or 0
        ) + 1
        message = BrainstormMessage(
            session_id=session_id,
            sequence_number=sequence,
            role=role,
            agent_name=agent_name,
            content=content,
            payload=payload or {},
            evidence_ids=evidence_ids or [],
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def latest_version(self, session_id: UUID) -> BrainstormVersion | None:
        value: BrainstormVersion | None = await self._session.scalar(
            select(BrainstormVersion)
            .where(BrainstormVersion.session_id == session_id)
            .order_by(BrainstormVersion.version_number.desc())
            .limit(1)
        )
        return value

    async def original_version(self, session_id: UUID) -> BrainstormVersion | None:
        value: BrainstormVersion | None = await self._session.scalar(
            select(BrainstormVersion).where(
                BrainstormVersion.session_id == session_id,
                BrainstormVersion.kind == "original",
            )
        )
        return value

    async def add_version(
        self,
        *,
        session_id: UUID,
        kind: str,
        content: str,
        parent_version_id: UUID | None,
        source_filename: str | None = None,
        source_media_type: str | None = None,
        source_hash: str | None = None,
        change_summary: list[dict[str, object]] | None = None,
    ) -> BrainstormVersion:
        latest = await self.latest_version(session_id)
        version_number = (latest.version_number if latest else 0) + 1
        version = BrainstormVersion(
            session_id=session_id,
            parent_version_id=parent_version_id,
            version_number=version_number,
            kind=kind,
            content=content,
            content_hash=source_hash or hashlib.sha256(content.encode("utf-8")).hexdigest(),
            source_filename=source_filename,
            source_media_type=source_media_type,
            change_summary=change_summary or [],
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def restore_version(
        self,
        *,
        session_id: UUID,
        version_id: UUID,
    ) -> BrainstormVersion:
        history = await self.history(session_id)
        if history.session.status == "processing":
            raise ValueError("The brainstorm session is processing a turn")
        if history.session.status == "finalized":
            raise ValueError("A finalized brainstorm session cannot be restored")
        source = next((value for value in history.versions if value.id == version_id), None)
        if source is None:
            raise ValueError("Restore point does not belong to this session")
        restored = await self.add_version(
            session_id=session_id,
            kind="restore",
            content=source.content,
            parent_version_id=source.id,
            change_summary=[
                {
                    "action": "restored_from_version",
                    "source_version": source.version_number,
                }
            ],
        )
        await self.append_message(
            session_id=session_id,
            role="system",
            agent_name="version_control",
            content=(
                f"已还原到方案 V{source.version_number}，"
                f"并创建非破坏性分支 V{restored.version_number}。"
            ),
            payload={
                "version_id": str(restored.id),
                "version_number": restored.version_number,
                "restored_from_version_id": str(source.id),
                "restored_from_version_number": source.version_number,
            },
        )
        history.session.status = "active"
        await self._session.commit()
        return restored

    async def set_processing(self, session: BrainstormSession) -> None:
        if session.status == "processing":
            raise ValueError("The brainstorm session is already processing a turn")
        if session.status == "finalized":
            raise ValueError("The brainstorm session is finalized")
        session.status = "processing"
        await self._session.commit()

    async def set_awaiting_confirmation(self, session: BrainstormSession) -> None:
        session.status = "awaiting_confirmation"
        session.confirmation_round += 1
        await self._session.commit()

    async def set_failed(self, session_id: UUID) -> None:
        session = await self._session.get(BrainstormSession, session_id)
        if session is not None:
            session.status = "active"
            await self._session.commit()

    async def finalize(self, session_id: UUID) -> BrainstormVersion:
        history = await self.history(session_id)
        if history.session.status == "finalized":
            raise ValueError("The brainstorm session is already finalized")
        latest = history.versions[-1] if history.versions else None
        if latest is None:
            raise ValueError("There is no generated proposal to finalize")
        final = await self.add_version(
            session_id=session_id,
            kind="final",
            content=latest.content,
            parent_version_id=latest.id,
            change_summary=[{"action": "user_confirmed", "source_version": latest.version_number}],
        )
        history.session.status = "finalized"
        await self._session.commit()
        return final

    async def soft_delete(self, session_id: UUID, reason: str | None = None) -> None:
        session = await self._session.get(BrainstormSession, session_id)
        if session is None or session.deleted_at is not None:
            raise ValueError("Brainstorm session does not exist")
        session.deleted_at = datetime.now(UTC)
        session.delete_reason = reason
        facts = (
            await self._session.scalars(
                select(ProjectFact).where(
                    ProjectFact.source_session_id == session_id,
                    ProjectFact.deleted_at.is_(None),
                )
            )
        ).all()
        for fact in facts:
            fact.deleted_at = session.deleted_at
        await self._session.commit()
