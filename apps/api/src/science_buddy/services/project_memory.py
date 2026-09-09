import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    BrainstormMessage,
    BrainstormSessionMemory,
    ProjectFact,
    ProjectFactRelation,
)
from science_buddy.services.context_selection import select_markdown_context
from science_buddy.services.memory_context import MemoryContextService

_FACT_LIMIT = 80
_CATEGORY_CONFIDENCE = {
    "scientific_question": 0.90,
    "hypothesis": 0.82,
    "prediction": 0.72,
    "objective": 0.85,
    "work_package": 0.78,
    "analysis_plan": 0.80,
    "reproducibility": 0.85,
    "assumption": 0.60,
    "limitation": 0.75,
    "risk": 0.78,
    "safety": 0.92,
    "evidence_gap": 0.88,
    "user_preference": 1.0,
    "chosen_direction": 1.0,
}
_CATEGORY_IMPORTANCE = {
    "scientific_question": 1.00,
    "hypothesis": 0.95,
    "prediction": 0.75,
    "objective": 0.90,
    "work_package": 0.82,
    "analysis_plan": 0.82,
    "reproducibility": 0.80,
    "assumption": 0.72,
    "limitation": 0.78,
    "risk": 0.86,
    "safety": 1.00,
    "evidence_gap": 0.88,
    "user_preference": 0.95,
    "chosen_direction": 1.0,
}


@dataclass(frozen=True, slots=True)
class FactDraft:
    category: str
    statement: str
    field_path: str
    evidence_ids: tuple[str, ...] = ()


def _normalize(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append_scalar(
    drafts: list[FactDraft],
    payload: dict[str, Any],
    key: str,
    category: str,
    prefix: str,
) -> None:
    value = _normalize(payload.get(key))
    if value:
        drafts.append(FactDraft(category, value, f"{prefix}.{key}"))


def _append_list(
    drafts: list[FactDraft],
    payload: dict[str, Any],
    key: str,
    category: str,
    prefix: str,
) -> None:
    values = payload.get(key)
    if not isinstance(values, list):
        return
    for index, value in enumerate(values):
        statement = _normalize(value)
        if statement:
            drafts.append(FactDraft(category, statement, f"{prefix}.{key}[{index}]"))


def extract_fact_drafts(payload: dict[str, object]) -> list[FactDraft]:
    drafts: list[FactDraft] = []
    question = payload.get("question_agent")
    if isinstance(question, dict):
        typed = cast(dict[str, Any], question)
        _append_scalar(
            drafts,
            typed,
            "scientific_question",
            "scientific_question",
            "question_agent",
        )
        _append_scalar(drafts, typed, "hypothesis", "hypothesis", "question_agent")
        for key, category in (
            ("predictions", "prediction"),
            ("assumptions", "assumption"),
            ("evidence_gaps", "evidence_gap"),
        ):
            _append_list(drafts, typed, key, category, "question_agent")

    experiment = payload.get("experiment_agent")
    if isinstance(experiment, dict):
        typed = cast(dict[str, Any], experiment)
        _append_scalar(drafts, typed, "design_summary", "objective", "experiment_agent")
        for key, category in (
            ("objectives", "objective"),
            ("analysis_plan", "analysis_plan"),
            ("reproducibility_plan", "reproducibility"),
            ("evidence_gaps", "evidence_gap"),
            ("safety_flags", "safety"),
        ):
            _append_list(drafts, typed, key, category, "experiment_agent")
        packages = typed.get("work_packages")
        if isinstance(packages, list):
            for index, item in enumerate(packages):
                if not isinstance(item, dict):
                    continue
                title = _normalize(item.get("title"))
                purpose = _normalize(item.get("purpose"))
                approach = _normalize(item.get("approach"))
                statement = " — ".join(value for value in (title, purpose, approach) if value)
                if statement:
                    drafts.append(
                        FactDraft(
                            "work_package",
                            statement,
                            f"experiment_agent.work_packages[{index}]",
                            tuple(str(value) for value in item.get("evidence_ids", [])),
                        )
                    )

    critic = payload.get("critic_agent")
    if isinstance(critic, dict):
        typed = cast(dict[str, Any], critic)
        for key, category in (
            ("limitations", "limitation"),
            ("feasibility_risks", "risk"),
            ("fatal_flaws", "risk"),
            ("safety_flags", "safety"),
        ):
            _append_list(drafts, typed, key, category, "critic_agent")

    coordinator = payload.get("coordinator")
    if isinstance(coordinator, dict):
        typed = cast(dict[str, Any], coordinator)
        _append_list(drafts, typed, "safety_flags", "safety", "coordinator")

    selected_direction = _normalize(payload.get("selected_direction_id"))
    if selected_direction:
        drafts.append(
            FactDraft(
                "chosen_direction",
                selected_direction,
                "selected_direction_id",
            )
        )
    preference_profile = payload.get("preference_profile")
    if isinstance(preference_profile, dict):
        for key, value in preference_profile.items():
            if value in (None, "", []):
                continue
            statement = f"{key}: {json.dumps(value, ensure_ascii=False)}"
            drafts.append(
                FactDraft(
                    "user_preference",
                    statement,
                    f"preference_profile.{key}",
                )
            )

    deduplicated: dict[tuple[str, str], FactDraft] = {}
    for draft in drafts:
        identity = (draft.category, _normalize(draft.statement).casefold())
        deduplicated.setdefault(identity, draft)
    return list(deduplicated.values())[:_FACT_LIMIT]


def build_session_summary(
    *,
    session_title: str,
    turn_number: int,
    payload: dict[str, object],
) -> tuple[str, dict[str, Any]]:
    drafts = extract_fact_drafts(payload)
    grouped: dict[str, list[str]] = {}
    for draft in drafts:
        grouped.setdefault(draft.category, []).append(draft.statement)
    labels = {
        "scientific_question": "科学问题",
        "hypothesis": "核心假设",
        "objective": "研究目标",
        "work_package": "实验工作包",
        "analysis_plan": "分析计划",
        "reproducibility": "可重复性",
        "limitation": "局限",
        "risk": "风险",
        "safety": "安全与伦理",
        "evidence_gap": "证据缺口",
        "user_preference": "用户实验偏好",
        "chosen_direction": "已选研究方向",
    }
    lines = [f"# {session_title}", "", f"截至第 {turn_number} 轮的确定性摘要。"]
    for category, label in labels.items():
        values = grouped.get(category, [])
        if not values:
            continue
        lines.extend(["", f"## {label}"])
        lines.extend(f"- {value}" for value in values[:8])
    summary_data = {
        "turn_number": turn_number,
        "categories": grouped,
        "fact_count": len(drafts),
    }
    return "\n".join(lines), summary_data


class ProjectMemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_brainstorm_turn(
        self,
        *,
        project_id: UUID,
        session_id: UUID,
        message: BrainstormMessage,
        turn_number: int,
        session_title: str,
    ) -> tuple[BrainstormSessionMemory, list[ProjectFact]]:
        summary_markdown, summary_data = build_session_summary(
            session_title=session_title,
            turn_number=turn_number,
            payload=message.payload,
        )
        summary_hash = _hash(
            json.dumps(summary_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        statement = sqlite_insert(BrainstormSessionMemory).values(
            project_id=project_id,
            session_id=session_id,
            source_message_id=message.id,
            turn_number=turn_number,
            summary_markdown=summary_markdown,
            summary_data=summary_data,
            content_hash=summary_hash,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[BrainstormSessionMemory.session_id],
            set_={
                "source_message_id": message.id,
                "turn_number": turn_number,
                "summary_markdown": summary_markdown,
                "summary_data": summary_data,
                "content_hash": summary_hash,
            },
        )
        await self._session.execute(statement)

        for draft in extract_fact_drafts(message.payload):
            statement_text = _normalize(draft.statement)
            locator = {
                "session_id": str(session_id),
                "message_id": str(message.id),
                "message_sequence": message.sequence_number,
                "turn_number": turn_number,
                "field_path": draft.field_path,
                "evidence_ids": list(draft.evidence_ids),
            }
            fact_statement = sqlite_insert(ProjectFact).values(
                project_id=project_id,
                category=draft.category,
                statement=statement_text,
                statement_hash=_hash(statement_text.casefold()),
                source_type="brainstorm_message",
                source_id=message.id,
                source_session_id=session_id,
                source_locator=locator,
                confidence=_CATEGORY_CONFIDENCE[draft.category],
                importance=_CATEGORY_IMPORTANCE[draft.category],
                status="active",
            )
            await self._session.execute(
                fact_statement.on_conflict_do_update(
                    index_elements=[
                        ProjectFact.project_id,
                        ProjectFact.category,
                        ProjectFact.statement_hash,
                        ProjectFact.source_type,
                        ProjectFact.source_id,
                    ],
                    set_={
                        "source_locator": locator,
                        "confidence": _CATEGORY_CONFIDENCE[draft.category],
                        "importance": _CATEGORY_IMPORTANCE[draft.category],
                        "status": "active",
                        "deleted_at": None,
                    },
                )
            )
        await self._session.flush()
        memory = await self.latest_session_memory(session_id)
        if memory is None:
            raise RuntimeError("Session memory was not created")
        facts = await self.list_facts(project_id, source_session_id=session_id, limit=100)
        return memory, facts

    async def latest_session_memory(self, session_id: UUID) -> BrainstormSessionMemory | None:
        return cast(
            BrainstormSessionMemory | None,
            await self._session.scalar(
                select(BrainstormSessionMemory).where(
                    BrainstormSessionMemory.session_id == session_id
                )
            ),
        )

    async def list_facts(
        self,
        project_id: UUID,
        *,
        source_session_id: UUID | None = None,
        category: str | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[ProjectFact]:
        query = select(ProjectFact).where(
            ProjectFact.project_id == project_id,
            ProjectFact.deleted_at.is_(None),
            ProjectFact.status == status,
        )
        if source_session_id is not None:
            query = query.where(ProjectFact.source_session_id == source_session_id)
        if category:
            query = query.where(ProjectFact.category == category)
        return list(
            (
                await self._session.scalars(
                    query.order_by(
                        ProjectFact.importance.desc(),
                        ProjectFact.confidence.desc(),
                        ProjectFact.updated_at.desc(),
                    ).limit(limit)
                )
            ).all()
        )

    async def context_pack(
        self,
        project_id: UUID,
        session_id: UUID,
        *,
        query: str = "",
        char_budget: int = 12000,
    ) -> dict[str, object]:
        memory = await self.latest_session_memory(session_id)
        facts = await self.list_facts(project_id, limit=24)
        summary_selection = (
            select_markdown_context(
                memory.summary_markdown,
                query=query,
                char_budget=char_budget,
            )
            if memory
            else None
        )
        memory_context: dict[str, object] | None = None
        if query.strip():
            memory_context = (
                await MemoryContextService(self._session).recall(
                    project_id=project_id,
                    query=query,
                    consumer="brainstorm",
                    statuses=("active", "superseded", "retracted"),
                    min_confidence=0.5,
                    limit=12,
                )
            ).to_payload()
        context_items = memory_context.get("items") if memory_context else None
        return {
            "session_summary": (
                summary_selection.to_payload() if summary_selection else None
            ),
            "project_facts": [
                {
                    "category": fact.category,
                    "statement": fact.statement,
                    "confidence": fact.confidence,
                    "importance": fact.importance,
                    "source_type": fact.source_type,
                    "source_id": str(fact.source_id),
                    "source_locator": fact.source_locator,
                }
                for fact in facts
            ],
            "memory_context": memory_context,
            "hybrid_semantic_memory": (
                context_items if isinstance(context_items, list) else []
            ),
        }

    async def update_fact_status(
        self,
        *,
        project_id: UUID,
        fact_id: UUID,
        status: str,
    ) -> ProjectFact:
        if status not in {"active", "superseded", "retracted"}:
            raise ValueError("Unsupported fact status")
        fact = await self._session.get(ProjectFact, fact_id)
        if fact is None or fact.project_id != project_id or fact.deleted_at is not None:
            raise LookupError("Project fact not found")
        fact.status = status
        await self._session.commit()
        await self._session.refresh(fact)
        return fact

    async def relate_facts(
        self,
        *,
        project_id: UUID,
        source_fact_id: UUID,
        target_fact_id: UUID,
        relation_type: str,
    ) -> ProjectFactRelation:
        if relation_type not in {"conflicts_with", "supersedes", "synonym_of"}:
            raise ValueError("Unsupported fact relation")
        if source_fact_id == target_fact_id:
            raise ValueError("A fact cannot relate to itself")
        source = await self._session.get(ProjectFact, source_fact_id)
        target = await self._session.get(ProjectFact, target_fact_id)
        if (
            source is None
            or target is None
            or source.project_id != project_id
            or target.project_id != project_id
            or source.deleted_at is not None
            or target.deleted_at is not None
        ):
            raise LookupError("Project fact not found")
        relation = await self._session.scalar(
            select(ProjectFactRelation).where(
                ProjectFactRelation.source_fact_id == source_fact_id,
                ProjectFactRelation.relation_type == relation_type,
                ProjectFactRelation.target_fact_id == target_fact_id,
            )
        )
        if relation is None:
            relation = ProjectFactRelation(
                project_id=project_id,
                source_fact_id=source_fact_id,
                target_fact_id=target_fact_id,
                relation_type=relation_type,
                provenance={"source": "user_confirmation"},
            )
            self._session.add(relation)
        if relation_type == "supersedes":
            target.status = "superseded"
        await self._session.commit()
        await self._session.refresh(relation)
        return relation
