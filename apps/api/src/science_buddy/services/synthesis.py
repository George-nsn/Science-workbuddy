from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import fitz  # type: ignore[import-untyped]
import httpx
from docx import Document
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from science_buddy.config import Settings
from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.providers import (
    LiteratureProvider,
    ModelProvider,
    StructuredGenerationRequest,
    TextGenerationRequest,
)
from science_buddy.infrastructure.models import (
    Project,
    SynthesisAgentRun,
    SynthesisLiterature,
    SynthesisReviewRound,
    SynthesisSection,
    SynthesisSession,
    SynthesisSource,
    WorkbenchAttachment,
    WorkbenchNote,
)
from science_buddy.services.citations import (
    citation_map_for_candidates,
    reference_markdown,
)
from science_buddy.services.context_selection import (
    select_markdown_context,
    split_markdown_modules,
)
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.literature import (
    CrossrefProvider,
    EuropePmcProvider,
    OpenAlexProvider,
    PubMedProvider,
)
from science_buddy.services.literature.discovery import (
    group_literature_records,
    interleave_literature_batches,
)
from science_buddy.services.literature.ingestion import LiteratureIngestionService
from science_buddy.services.memory_context import MemoryContextService
from science_buddy.services.models import ModelResponseError, drain_model_usage
from science_buddy.services.plot_agent import (
    PlotAgentError,
    PlotRequest,
    parse_markdown_table,
    render_plot,
)
from science_buddy.services.reranking import get_reranker_service
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever
from science_buddy.services.route_memory import RetrievalRouteMemoryService
from science_buddy.services.synthesis_template import distilled_thesis_profile
from science_buddy.services.synthesis_types import (
    DocumentUnderstandingOutput,
    FigurePlan,
    GlobalReview,
    SectionDigest,
    SectionReview,
    SynthesisOutlineSectionSpec,
)
from science_buddy.services.usage import UsageService

_EVIDENCE_PATTERN = re.compile(r"ev1\.[A-Za-z0-9._-]+")
_SOURCE_PATTERN = re.compile(r"\[SRC:([0-9a-fA-F-]{36})\]")


class SynthesisError(ValueError):
    pass


def content_hash(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def extract_source_text(path: Path, media_type: str) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".pdf" or media_type == "application/pdf":
        document = fitz.open(path)
        try:
            text = "\n\n".join(page.get_text("text") for page in document)
        finally:
            document.close()
    elif suffix == ".docx" or media_type.endswith("wordprocessingml.document"):
        document = Document(str(path))
        text = "\n\n".join(
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        )
    elif suffix in {".txt", ".md", ".markdown"} or media_type.startswith("text/"):
        text = path.read_text(encoding="utf-8-sig", errors="strict")
    else:
        raise SynthesisError("仅支持 PDF、DOCX、TXT 和 Markdown 补充材料")
    normalized = text.strip()
    if not normalized:
        raise SynthesisError("上传内容没有可提取文本")
    if len(normalized) > 2_000_000:
        raise SynthesisError("单个补充材料提取文本不能超过 200 万字符")
    return normalized


def module_payload(value: str) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": module.ordinal,
            "section_path": list(module.path),
            "chunk_index": module.chunk_index,
            "characters": len(module.content),
            "stable": module.stable,
        }
        for module in split_markdown_modules(value)
    ]


def markdown_tables(value: str) -> list[str]:
    tables: list[str] = []
    current: list[str] = []
    for raw_line in [*value.splitlines(), ""]:
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            current.append(line)
            continue
        if len(current) >= 3:
            candidate = "\n".join(current)
            try:
                parse_markdown_table(candidate)
            except PlotAgentError:
                pass
            else:
                tables.append(candidate)
        current = []
    return tables


def context_char_budget(
    max_context_tokens: int,
    *,
    allocation: float,
    cap: int,
) -> int:
    """Reserve model space while conservatively mapping selected text chars to tokens."""
    return max(12_000, min(cap, int(max_context_tokens * allocation)))


def output_token_budget(max_context_tokens: int, *, cap: int) -> int:
    return min(cap, max(4096, max_context_tokens // 4))


def _fallback_understanding(topic: str) -> DocumentUnderstandingOutput:
    sections = [
        ("abstract", "摘要与关键词", "概括研究问题、材料范围、核心综合结论与证据边界"),
        ("introduction", "第1章 绪论", "界定背景、文献缺口、核心问题和全文路线"),
        ("review", "第2章 文献综述与理论框架", "按主题整合研究进展、共识、冲突与不足"),
        ("methods", "第3章 资料来源与综合方法", "说明资料来源、纳入逻辑、分析维度和引用边界"),
        ("synthesis", "第4章 主题综合与关系分析", "结合笔记、补充材料与文献证据展开核心论证"),
        ("discussion", "第5章 讨论", "解释机制、替代解释、局限、适用范围与未决问题"),
        ("conclusion", "第6章 结论与展望", "凝练已支持结论并提出可验证的后续方向"),
    ]
    return DocumentUnderstandingOutput(
        working_title=topic[:300],
        central_question=topic,
        thesis_statement="围绕当前主题整合项目笔记、补充材料与可追溯文献证据。",
        global_summary="使用章节化证据综合建立完整、可回溯且术语一致的长文。",
        outline=[
            SynthesisOutlineSectionSpec(
                section_key=key,
                title=title,
                purpose=purpose,
                outline=[],
                retrieval_queries=[topic],
                target_words=800 if key == "abstract" else 1600,
            )
            for key, title, purpose in sections
        ],
        literature_queries=[topic],
    )


class SynthesisOrchestrator:
    """Chapter-state writing pipeline with source, evidence and review provenance."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        model: ModelProvider,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._db = session
        self._model = model
        self._settings = settings
        self._session_factory = session_factory
        self._workflow_id = uuid4()
        self._candidates: dict[str, RetrievalCandidate] = {}

    async def run(self, synthesis: SynthesisSession) -> None:
        synthesis.status = "analyzing"
        synthesis.error_message = None
        synthesis.template_profile = distilled_thesis_profile()
        await self._db.commit()
        await self._sync_workbench_sources(synthesis)
        sources = await self._sources(synthesis.id)
        understanding = await self._understand(synthesis, sources)
        if synthesis.allow_online_literature and understanding.literature_queries:
            await self._supplement_literature(
                synthesis,
                understanding.literature_queries,
            )
        await self._apply_understanding(synthesis, sources, understanding)
        await self._generate_figures(synthesis, sources, understanding.figure_plan)
        synthesis.status = "writing"
        await self._db.commit()
        sections = await self._sections(synthesis.id)
        for section in sections:
            await self._write_section(synthesis, section, sources)
        await self._global_review_loop(synthesis, sources)
        synthesis.manuscript_markdown = await self._assemble(synthesis)
        synthesis.status = "completed"
        await self._record_usage(synthesis.project_id)
        await self._db.commit()

    async def _sync_workbench_sources(self, synthesis: SynthesisSession) -> None:
        if not synthesis.include_workbench_notes:
            return
        notes = list(
            (
                await self._db.scalars(
                    select(WorkbenchNote)
                    .where(
                        WorkbenchNote.project_id == synthesis.project_id,
                        WorkbenchNote.deleted_at.is_(None),
                    )
                    .order_by(WorkbenchNote.entry_date)
                )
            ).all()
        )
        for note in notes:
            text = note.content_markdown.strip()
            if not text:
                continue
            digest = content_hash(text)
            existing = await self._db.scalar(
                select(SynthesisSource).where(
                    SynthesisSource.session_id == synthesis.id,
                    SynthesisSource.content_hash == digest,
                )
            )
            if existing is None:
                self._db.add(
                    SynthesisSource(
                        session_id=synthesis.id,
                        source_type="workbench_note",
                        source_ref_id=note.id,
                        filename=f"{note.entry_date.isoformat()} · {note.title}",
                        media_type="text/markdown",
                        content_hash=digest,
                        extracted_text=text,
                        module_index=module_payload(text),
                        status="ready",
                    )
                )
        attachments = list(
            (
                await self._db.scalars(
                    select(WorkbenchAttachment).where(
                        WorkbenchAttachment.project_id == synthesis.project_id,
                        WorkbenchAttachment.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        synthesis.figure_manifest = [
            {
                "attachment_id": str(value.id),
                "filename": value.filename,
                "caption": value.caption,
                "kind": value.attachment_kind,
                "content_url": f"/api/v1/workbench/attachments/{value.id}/content",
                "render_spec": (
                    value.render_spec if value.attachment_kind == "generated_plot" else {}
                ),
                "ocr_text": value.ocr_text[:2000] if value.ocr_text else "",
            }
            for value in attachments
            if value.attachment_kind == "generated_plot" or value.ocr_text
        ]
        await self._db.commit()

    async def _sources(self, session_id: UUID) -> list[SynthesisSource]:
        return list(
            (
                await self._db.scalars(
                    select(SynthesisSource)
                    .where(SynthesisSource.session_id == session_id)
                    .order_by(SynthesisSource.created_at)
                )
            ).all()
        )

    async def _sections(self, session_id: UUID) -> list[SynthesisSection]:
        return list(
            (
                await self._db.scalars(
                    select(SynthesisSection)
                    .where(SynthesisSection.session_id == session_id)
                    .order_by(SynthesisSection.ordinal)
                )
            ).all()
        )

    def _source_context(
        self,
        sources: Sequence[SynthesisSource],
        *,
        query: str,
        budget: int,
    ) -> list[dict[str, Any]]:
        per_source = max(1800, budget // max(1, min(len(sources), 12)))
        values: list[dict[str, Any]] = []
        used = 0
        for source in sources:
            selection = select_markdown_context(
                source.extracted_text,
                query=query,
                char_budget=per_source,
            )
            selected = selection.to_payload()
            size = int(selected["selected_characters"])
            if size == 0 or used + size > budget:
                continue
            used += size
            values.append(
                {
                    "source_id": str(source.id),
                    "filename": source.filename,
                    "source_type": source.source_type,
                    "selection": selected,
                }
            )
        return values

    def _table_inventory(
        self,
        sources: Sequence[SynthesisSource],
    ) -> list[dict[str, Any]]:
        inventory: list[dict[str, Any]] = []
        for source in sources:
            for table_index, markdown in enumerate(markdown_tables(source.extracted_text)):
                table = parse_markdown_table(markdown)
                inventory.append(
                    {
                        "source_id": str(source.id),
                        "filename": source.filename,
                        "table_index": table_index,
                        "headers": list(table.headers),
                        "row_count": len(table.rows),
                        "column_types": table.column_types,
                        "preview": markdown[:3000],
                    }
                )
                if len(inventory) >= 30:
                    return inventory
        return inventory

    async def _understand(
        self,
        synthesis: SynthesisSession,
        sources: Sequence[SynthesisSource],
    ) -> DocumentUnderstandingOutput:
        memory_context = await MemoryContextService(self._db).recall(
            project_id=synthesis.project_id,
            query=synthesis.topic,
            consumer="synthesis",
            statuses=("active", "superseded", "retracted"),
            min_confidence=0.5,
            limit=20,
        )
        context = self._source_context(
            sources,
            query=synthesis.topic,
            budget=context_char_budget(
                synthesis.max_context_tokens,
                allocation=0.4,
                cap=400_000,
            ),
        )
        request = StructuredGenerationRequest(
            system_instruction=(
                "You are the Document Understanding Agent for a life-science thesis/review. "
                "Infer relationships among sources, build a stable terminology map and propose "
                "a complete chapter outline. Adapt the distilled structural profile to the actual "
                "topic rather than copying a thesis directory. Treat workbench notes and uploads "
                "as source material; distinguish support, complement, contradiction and sequence. "
                "Plan figures only from a supplied real table source_id and table_index. Return "
                "concise structured planning data, not thesis prose."
            ),
            user_content=json.dumps(
                {
                    "topic": synthesis.topic,
                    "template_profile": synthesis.template_profile,
                    "existing_outline": synthesis.global_outline,
                    "sources": context,
                    "figure_inventory": synthesis.figure_manifest,
                    "table_inventory": self._table_inventory(sources),
                    "memory_context": memory_context.to_payload(),
                },
                ensure_ascii=False,
                default=str,
            ),
            response_schema=DocumentUnderstandingOutput.model_json_schema(),
            operation="synthesis.document_understanding",
            depth=cast(Literal["quick", "balanced", "deep", "max"], synthesis.model_depth),
            max_context_tokens=synthesis.max_context_tokens,
            max_output_tokens=output_token_budget(
                synthesis.max_context_tokens,
                cap=24576,
            ),
        )
        try:
            raw = await self._model.generate_structured(request)
            output = DocumentUnderstandingOutput.model_validate(raw)
        except (ModelResponseError, ValidationError) as exc:
            output = _fallback_understanding(synthesis.topic)
            await self._agent_run(
                synthesis,
                "document_understanding_fallback",
                0,
                None,
                {"fallback": output.model_dump(mode="json")},
                error=str(exc)[:1000],
            )
        else:
            await self._agent_run(
                synthesis,
                "document_understanding",
                0,
                None,
                output.model_dump(mode="json"),
            )
        return output

    async def _apply_understanding(
        self,
        synthesis: SynthesisSession,
        sources: Sequence[SynthesisSource],
        output: DocumentUnderstandingOutput,
    ) -> None:
        synthesis.title = output.working_title
        synthesis.global_summary = output.global_summary
        synthesis.glossary = output.glossary
        synthesis.document_map = {
            "central_question": output.central_question,
            "thesis_statement": output.thesis_statement,
            "source_relations": [item.model_dump(mode="json") for item in output.source_relations],
        }
        for source in sources:
            source.relations = [
                item.model_dump(mode="json")
                for item in output.source_relations
                if str(source.id) in item.source_ids
            ]
        existing_sections = {
            value.section_key: value for value in await self._sections(synthesis.id)
        }
        by_key: dict[str, SynthesisSection] = {}
        raw_to_safe: dict[str, str] = {}
        normalized_outline: list[dict[str, Any]] = []
        for ordinal, item in enumerate(output.outline):
            key = re.sub(r"[^a-zA-Z0-9_-]+", "-", item.section_key).strip("-")[:96]
            key = key or f"section-{ordinal + 1}"
            if key in by_key:
                key = f"{key}-{ordinal + 1}"
            raw_to_safe[item.section_key] = key
            normalized = item.model_dump(mode="json")
            normalized["section_key"] = key
            normalized_outline.append(normalized)
            section = existing_sections.pop(key, None)
            if section is None:
                section = SynthesisSection(
                    session_id=synthesis.id,
                    section_key=key,
                    status="pending",
                )
                self._db.add(section)
            section.ordinal = ordinal
            section.level = item.level
            section.title = item.title
            section.purpose = item.purpose
            section.outline = item.outline
            by_key[key] = section
        for stale in existing_sections.values():
            await self._db.delete(stale)
        await self._db.flush()
        for item, normalized, section in zip(
            output.outline,
            normalized_outline,
            by_key.values(),
            strict=False,
        ):
            if item.parent_key:
                safe_parent_key = raw_to_safe.get(item.parent_key, item.parent_key)
                normalized["parent_key"] = safe_parent_key
                parent = by_key.get(safe_parent_key)
                section.parent_id = parent.id if parent is not None else None
            else:
                section.parent_id = None
        synthesis.global_outline = normalized_outline
        await self._db.commit()

    async def _generate_figures(
        self,
        synthesis: SynthesisSession,
        sources: Sequence[SynthesisSource],
        requested_plans: Sequence[FigurePlan],
    ) -> None:
        source_tables = {
            str(source.id): markdown_tables(source.extracted_text) for source in sources
        }
        plans = list(requested_plans)
        if not plans:
            for source in sources:
                for table_index, markdown in enumerate(source_tables[str(source.id)]):
                    table = parse_markdown_table(markdown)
                    if not any(value == "numeric" for value in table.column_types.values()):
                        continue
                    plans.append(
                        FigurePlan(
                            source_id=str(source.id),
                            table_index=table_index,
                            title=f"{source.filename} · 数据概览",
                            caption="由补充材料中的结构化数据表自动生成。",
                        )
                    )
                    if len(plans) >= 4:
                        break
                if len(plans) >= 4:
                    break
        generated: list[dict[str, Any]] = []
        for plan in plans[:8]:
            tables = source_tables.get(plan.source_id, [])
            if plan.table_index >= len(tables):
                continue
            markdown = tables[plan.table_index]
            table = parse_markdown_table(markdown)
            figure_id = content_hash(
                f"{plan.source_id}:{plan.table_index}:{table.source_hash}:{plan.chart_type}"
            )[:24]
            target = (
                self._settings.upload_directory
                / "synthesis"
                / synthesis.project_id.hex
                / synthesis.id.hex
                / "figures"
                / f"{figure_id}.png"
            )
            try:
                artifact = await asyncio.to_thread(
                    render_plot,
                    table,
                    PlotRequest(
                        chart_type=plan.chart_type,
                        title=plan.title,
                        caption=plan.caption or None,
                        dpi=300,
                    ),
                    target,
                )
            except PlotAgentError:
                continue
            generated.append(
                {
                    "figure_id": figure_id,
                    "source_id": plan.source_id,
                    "table_index": plan.table_index,
                    "title": plan.title,
                    "caption": plan.caption,
                    "chart_type": artifact.chart_type,
                    "section_keys": plan.section_keys,
                    "content_url": (
                        f"/api/v1/synthesis/sessions/{synthesis.id}/figures/{figure_id}"
                    ),
                    "storage_path": str(artifact.path),
                    "source_table_hash": artifact.source_table_hash,
                    "source_table_markdown": markdown,
                    "render_spec": artifact.render_spec,
                    "warnings": list(artifact.warnings),
                    "generator": "synthesis-plot-agent-v1",
                }
            )
        synthesis.figure_manifest = [*synthesis.figure_manifest, *generated]
        await self._db.commit()

    async def _supplement_literature(
        self,
        synthesis: SynthesisSession,
        queries: Sequence[str],
    ) -> None:
        async with httpx.AsyncClient(
            timeout=self._settings.literature_request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            providers = self._literature_providers(client)
            selected_queries = list(
                dict.fromkeys(query.strip()[:500] for query in queries if query.strip())
            )[:3]
            calls = [
                (query, provider.search(query, limit=5))
                for query in selected_queries
                for provider in providers
            ]
            results = await asyncio.gather(
                *(operation for _, operation in calls),
                return_exceptions=True,
            )
            batches = [value for value in results if isinstance(value, list)]
            records = interleave_literature_batches(batches)
            selected = group_literature_records(records)[:20]
            ingestor = LiteratureIngestionService(
                self._db,
                default_project_name=self._settings.default_project_name,
            )
            for _candidate, source_records in selected:
                if not source_records:
                    continue
                result = await ingestor.ingest(
                    source_records[0],
                    project_id=synthesis.project_id,
                )
                query = next(
                    (
                        item_query
                        for (item_query, _), response in zip(calls, results, strict=True)
                        if isinstance(response, list)
                        and any(
                            record.provider == source_records[0].provider
                            and record.source_id == source_records[0].source_id
                            for record in response
                        )
                    ),
                    selected_queries[0],
                )
                existing = await self._db.get(
                    SynthesisLiterature,
                    (synthesis.id, result.paper_id),
                )
                if existing is None:
                    self._db.add(
                        SynthesisLiterature(
                            session_id=synthesis.id,
                            paper_id=result.paper_id,
                            query=query,
                            origin="online_discovery",
                        )
                    )
            await self._db.commit()

    def _literature_providers(self, client: httpx.AsyncClient) -> list[LiteratureProvider]:
        return [
            PubMedProvider(
                client,
                base_url=self._settings.ncbi_base_url,
                tool=self._settings.ncbi_tool,
                email=self._settings.ncbi_email,
                api_key=(
                    self._settings.ncbi_api_key.get_secret_value()
                    if self._settings.ncbi_api_key
                    else None
                ),
            ),
            EuropePmcProvider(client, base_url=self._settings.europe_pmc_base_url),
            OpenAlexProvider(
                client,
                base_url=self._settings.openalex_base_url,
                api_key=(
                    self._settings.openalex_api_key.get_secret_value()
                    if self._settings.openalex_api_key
                    else None
                ),
            ),
            CrossrefProvider(client, base_url=self._settings.crossref_base_url),
        ]

    async def _retrieve(self, synthesis: SynthesisSession, query: str) -> list[RetrievalCandidate]:
        project = await self._db.get(Project, synthesis.project_id)
        if project is None:
            return []
        config = RetrievalConfig.from_settings(self._settings)
        retriever = SQLiteHybridRetriever(
            self._db,
            EvidenceTokenService(self._settings.evidence_signing_key.get_secret_value()),
            workflow_id=self._workflow_id,
            config=config,
            embedding_service=get_embedding_service(),
            session_factory=self._session_factory,
            reranker=(
                get_reranker_service(config.reranker_model)
                if config.reranker_enabled
                else None
            ),
        )
        try:
            candidates = await retriever.retrieve(
                query,
                project_id=synthesis.project_id,
                limit=12,
            )
        except RuntimeError as exc:
            await RetrievalRouteMemoryService(self._db).record(
                project_id=synthesis.project_id,
                workflow_id=self._workflow_id,
                workflow_kind="synthesis",
                synthesis_session_id=synthesis.id,
                query=query,
                tool="local_hybrid_search",
                scope={"project_id": str(synthesis.project_id)},
                result_count=0,
                status="failed",
                failure_reason=str(exc),
            )
            return []
        if not candidates:
            await RetrievalRouteMemoryService(self._db).record(
                project_id=synthesis.project_id,
                workflow_id=self._workflow_id,
                workflow_kind="synthesis",
                synthesis_session_id=synthesis.id,
                query=query,
                tool="local_hybrid_search",
                scope={"project_id": str(synthesis.project_id)},
                result_count=0,
                status="empty",
                failure_reason="no_candidates",
            )
        for item in candidates:
            self._candidates[item.evidence_id] = item
        return candidates

    async def _write_section(
        self,
        synthesis: SynthesisSession,
        section: SynthesisSection,
        sources: Sequence[SynthesisSource],
    ) -> None:
        outline_item = next(
            (
                item
                for item in synthesis.global_outline
                if item.get("section_key") == section.section_key
            ),
            {},
        )
        queries = [str(value) for value in outline_item.get("retrieval_queries", []) if value]
        query = "；".join([section.title, section.purpose, *queries[:2]])
        memory_context = await MemoryContextService(self._db).recall(
            project_id=synthesis.project_id,
            query=query,
            consumer="synthesis",
            statuses=("active", "superseded", "retracted"),
            min_confidence=0.5,
            limit=16,
        )
        verified_prior = [
            value.to_payload()
            for value in memory_context.items
            if value.role in {"confirmed_project_fact", "verified_claim_prior"}
        ]
        planning_experience = [
            value.to_payload()
            for value in memory_context.items
            if value.role in {"planning_experience", "route_failure_experience"}
        ]
        prioritized_query = "；".join(
            [query, *[str(value["text"])[:500] for value in verified_prior[:2]]]
        )
        candidates = await self._retrieve(synthesis, prioritized_query)
        source_context = self._source_context(
            sources,
            query=query,
            budget=context_char_budget(
                synthesis.max_context_tokens,
                allocation=0.55,
                cap=550_000,
            ),
        )
        previous = [
            {
                "section_key": value.section_key,
                "title": value.title,
                "summary": value.section_summary,
            }
            for value in await self._sections(synthesis.id)
            if value.ordinal < section.ordinal and value.section_summary
        ]
        evidence = [
            {
                "evidence_id": item.evidence_id,
                "text": item.text,
                "source_locator": item.source_locator,
            }
            for item in candidates
        ]
        system = (
            "You are the Thesis Writing Agent. Write this section as polished Chinese academic "
            "Markdown that fits the global manuscript, not as notes or an outline. Maintain the "
            "global thesis and glossary, connect to previous section summaries, distinguish source "
            "observations from interpretation, and cite only supplied Evidence IDs or local source "
            "markers [SRC:uuid]. Use figures from the inventory when they materially clarify the "
            "argument. Verified prior claims and confirmed ProjectFacts may guide the argument and "
            "retrieval priority, but their historical Evidence IDs are not valid citations in this "
            "workflow. Search-step and failed-route memory are planning experience only and cannot "
            "support conclusions. Cite factual claims only with the current evidence list or local "
            "source markers. Do not fabricate citations. Begin with a Markdown heading at the "
            "requested level and write a complete section."
        )
        payload = {
            "topic": synthesis.topic,
            "global_summary": synthesis.global_summary,
            "document_map": synthesis.document_map,
            "glossary": synthesis.glossary,
            "template_principles": synthesis.template_profile.get("writing_principles", []),
            "section": {
                "key": section.section_key,
                "title": section.title,
                "purpose": section.purpose,
                "level": section.level,
                "outline": section.outline,
                "target_words": outline_item.get("target_words", 1200),
            },
            "previous_section_summaries": previous,
            "source_context": source_context,
            "evidence": evidence,
            "verified_prior_context": verified_prior,
            "planning_experience_not_evidence": planning_experience,
            "figure_inventory": synthesis.figure_manifest,
            "previous_draft": section.draft_markdown or None,
        }
        is_iteration = bool(section.draft_markdown.strip())
        section_round = section.revision + 1
        draft = await self._model.generate_text(
            TextGenerationRequest(
                system_instruction=(
                    system
                    + (
                        " Improve the supplied previous_draft rather than starting over; preserve "
                        "verified content and explicit user edits unless evidence requires change."
                        if is_iteration
                        else ""
                    )
                ),
                user_content=json.dumps(payload, ensure_ascii=False, default=str),
                operation=(
                    f"synthesis.writer.{section.section_key}.iterate"
                    if is_iteration
                    else f"synthesis.writer.{section.section_key}.draft"
                ),
                depth=cast(Literal["quick", "balanced", "deep", "max"], synthesis.model_depth),
                max_context_tokens=synthesis.max_context_tokens,
                max_output_tokens=output_token_budget(
                    synthesis.max_context_tokens,
                    cap=32768,
                ),
            )
        )
        await self._agent_run(
            synthesis,
            "thesis_writer",
            section_round,
            section,
            {"draft_markdown": draft, "stage": "draft"},
        )
        review = await self._review_section(
            synthesis,
            section,
            draft,
            candidates,
            section_round,
        )
        await self._save_review(synthesis, section, section_round, review)
        revised_after_review = False
        if review.verdict == "revise" and synthesis.max_review_rounds > 0:
            draft = await self._model.generate_text(
                TextGenerationRequest(
                    system_instruction=(
                        "You are the Thesis Writing Agent revising one section. Preserve valid "
                        "content and citations, resolve the reviewer feedback, maintain "
                        "terminology "
                        "and return the complete revised Markdown section only."
                    ),
                    user_content=json.dumps(
                        {
                            "draft": draft,
                            "review": review.model_dump(mode="json"),
                            "global_summary": synthesis.global_summary,
                            "glossary": synthesis.glossary,
                            "allowed_evidence_ids": [item.evidence_id for item in candidates],
                        },
                        ensure_ascii=False,
                    ),
                    operation=f"synthesis.writer.{section.section_key}.revise",
                    depth=cast(Literal["quick", "balanced", "deep", "max"], synthesis.model_depth),
                    max_context_tokens=synthesis.max_context_tokens,
                    max_output_tokens=output_token_budget(
                        synthesis.max_context_tokens,
                        cap=49152,
                    ),
                )
            )
            await self._agent_run(
                synthesis,
                "thesis_writer_revision",
                section_round,
                section,
                {"draft_markdown": draft, "stage": "section_review_revision"},
            )
            revised_after_review = True
        draft, evidence_ids, source_ids = self._sanitize_citations(
            draft,
            candidates,
            sources,
        )
        digest = await self._digest_section(synthesis, section, draft, evidence_ids, source_ids)
        section.draft_markdown = draft
        section.section_summary = digest.summary
        section.evidence_ids = evidence_ids
        section.source_ids = source_ids
        section.status = "revised" if revised_after_review else "reviewed"
        section.revision += 1
        section.content_hash = content_hash(draft)
        self._update_citation_ledger(synthesis, section)
        synthesis.global_summary = await self._rebuild_global_summary(synthesis)
        await self._db.commit()

    async def _review_section(
        self,
        synthesis: SynthesisSession,
        section: SynthesisSection,
        draft: str,
        candidates: Sequence[RetrievalCandidate],
        round_number: int,
    ) -> SectionReview:
        request = StructuredGenerationRequest(
            system_instruction=(
                "You are the Thesis Review Agent. Review logic, semantic coherence, consistency "
                "with section purpose, citation membership and missing material. Do not rewrite. "
                "Approve when the section is academically coherent even if stylistic alternatives "
                "exist. Unknown citations are errors."
            ),
            user_content=json.dumps(
                {
                    "section": {"title": section.title, "purpose": section.purpose},
                    "draft": draft,
                    "allowed_evidence_ids": [item.evidence_id for item in candidates],
                    "global_summary": synthesis.global_summary,
                    "glossary": synthesis.glossary,
                },
                ensure_ascii=False,
            ),
            response_schema=SectionReview.model_json_schema(),
            operation=f"synthesis.reviewer.{section.section_key}",
            depth="balanced",
            max_context_tokens=min(synthesis.max_context_tokens, 131072),
            max_output_tokens=8192,
        )
        try:
            raw = await self._model.generate_structured(request)
            review = SectionReview.model_validate(raw)
        except (ModelResponseError, ValidationError) as exc:
            review = SectionReview(
                verdict="approved",
                concise_summary="审查模型不可用，保留写作草稿并依赖全稿审查。",
            )
            await self._agent_run(
                synthesis,
                "thesis_reviewer_fallback",
                round_number,
                section,
                review.model_dump(mode="json"),
                error=str(exc)[:1000],
            )
        else:
            await self._agent_run(
                synthesis,
                "thesis_reviewer",
                round_number,
                section,
                review.model_dump(mode="json"),
            )
        return review

    async def _digest_section(
        self,
        synthesis: SynthesisSession,
        section: SynthesisSection,
        draft: str,
        evidence_ids: list[str],
        source_ids: list[str],
    ) -> SectionDigest:
        request = StructuredGenerationRequest(
            system_instruction=(
                "Create a compact semantic ledger entry for the completed section. Preserve only "
                "its central claims, terminology and citation/source identifiers. Do not rewrite "
                "the section."
            ),
            user_content=json.dumps(
                {
                    "section_key": section.section_key,
                    "title": section.title,
                    "draft": draft,
                    "allowed_evidence_ids": evidence_ids,
                    "allowed_source_ids": source_ids,
                },
                ensure_ascii=False,
            ),
            response_schema=SectionDigest.model_json_schema(),
            operation=f"synthesis.section_digest.{section.section_key}",
            depth="quick",
            max_context_tokens=min(synthesis.max_context_tokens, 131072),
            max_output_tokens=4096,
        )
        try:
            raw = await self._model.generate_structured(request)
            return SectionDigest.model_validate(raw)
        except (ModelResponseError, ValidationError):
            selection = select_markdown_context(
                draft,
                query=section.purpose or section.title,
                char_budget=1800,
            )
            summary = "\n".join(
                str(item["content"])
                for item in selection.to_payload()["modules"]
            )[:3000]
            return SectionDigest(
                summary=summary or draft[:1800],
                evidence_ids=evidence_ids,
                source_ids=source_ids,
            )

    def _update_citation_ledger(
        self,
        synthesis: SynthesisSession,
        section: SynthesisSection,
    ) -> None:
        ledger = dict(synthesis.citation_ledger)
        ledger[section.section_key] = {
            "title": section.title,
            "revision": section.revision,
            "evidence_ids": list(section.evidence_ids),
            "source_ids": list(section.source_ids),
            "content_hash": section.content_hash,
        }
        synthesis.citation_ledger = ledger

    async def _rebuild_global_summary(self, synthesis: SynthesisSession) -> str:
        thesis_statement = str(synthesis.document_map.get("thesis_statement") or "").strip()
        entries = [
            f"[{section.section_key}] {section.title}\n{section.section_summary}"
            for section in await self._sections(synthesis.id)
            if section.section_summary
        ]
        combined = "\n\n".join([thesis_statement, *entries]).strip()
        return combined[-40_000:]

    def _sanitize_citations(
        self,
        draft: str,
        candidates: Sequence[RetrievalCandidate],
        sources: Sequence[SynthesisSource],
    ) -> tuple[str, list[str], list[str]]:
        allowed_evidence = {item.evidence_id for item in candidates}
        allowed_sources = {str(item.id) for item in sources}
        evidence_ids: list[str] = []
        source_ids: list[str] = []

        def evidence_replace(match: re.Match[str]) -> str:
            value = match.group(0)
            if value in allowed_evidence:
                evidence_ids.append(value)
                return value
            return ""

        def source_replace(match: re.Match[str]) -> str:
            value = match.group(1)
            if value in allowed_sources:
                source_ids.append(value)
                return match.group(0)
            return ""

        cleaned = _EVIDENCE_PATTERN.sub(evidence_replace, draft)
        cleaned = _SOURCE_PATTERN.sub(source_replace, cleaned)
        cleaned = re.sub(r"\[\s*\]", "", cleaned)
        return cleaned, list(dict.fromkeys(evidence_ids)), list(dict.fromkeys(source_ids))

    async def _global_review_loop(
        self,
        synthesis: SynthesisSession,
        sources: Sequence[SynthesisSource],
    ) -> None:
        existing_reviews = list(
            (
                await self._db.scalars(
                    select(SynthesisReviewRound).where(
                        SynthesisReviewRound.session_id == synthesis.id,
                        SynthesisReviewRound.scope == "global",
                    )
                )
            ).all()
        )
        round_base = max((value.round_number for value in existing_reviews), default=0)
        for iteration in range(1, synthesis.max_review_rounds + 1):
            round_number = round_base + iteration
            synthesis.status = "reviewing"
            synthesis.current_round = round_number
            manuscript = await self._assemble(synthesis, include_references=False)
            selection = select_markdown_context(
                manuscript,
                query=synthesis.topic,
                char_budget=context_char_budget(
                    synthesis.max_context_tokens,
                    allocation=0.5,
                    cap=500_000,
                ),
            )
            review = await self._global_review(synthesis, selection.to_payload())
            self._db.add(
                SynthesisReviewRound(
                    session_id=synthesis.id,
                    round_number=round_number,
                    scope="global",
                    verdict=review.verdict,
                    feedback=review.model_dump(mode="json"),
                    affected_section_keys=review.affected_section_keys,
                    status="completed",
                )
            )
            await self._db.commit()
            if review.verdict == "approved":
                break
            sections = await self._sections(synthesis.id)
            affected = set(review.affected_section_keys)
            targets = [value for value in sections if not affected or value.section_key in affected]
            if not targets:
                targets = sections
            for section in targets:
                revised = await self._model.generate_text(
                    TextGenerationRequest(
                        system_instruction=(
                            "Revise the complete Markdown section using the global thesis review. "
                            "Preserve correct citations and local source markers, resolve only "
                            "relevant logic/semantic/citation issues, and return the full section."
                        ),
                        user_content=json.dumps(
                            {
                                "section_key": section.section_key,
                                "draft": section.draft_markdown,
                                "global_review": review.model_dump(mode="json"),
                                "global_summary": synthesis.global_summary,
                                "glossary": synthesis.glossary,
                            },
                            ensure_ascii=False,
                        ),
                        operation=f"synthesis.writer.global_revision.{section.section_key}",
                        depth=cast(
                            Literal["quick", "balanced", "deep", "max"],
                            synthesis.model_depth,
                        ),
                        max_context_tokens=synthesis.max_context_tokens,
                        max_output_tokens=output_token_budget(
                            synthesis.max_context_tokens,
                            cap=49152,
                        ),
                    )
                )
                revised, evidence_ids, source_ids = self._sanitize_citations(
                    revised,
                    list(self._candidates.values()),
                    sources,
                )
                section.draft_markdown = revised
                section.evidence_ids = evidence_ids
                section.source_ids = source_ids
                section.revision += 1
                section.content_hash = content_hash(revised)
                section.status = "revised"
                digest = await self._digest_section(
                    synthesis,
                    section,
                    revised,
                    evidence_ids,
                    source_ids,
                )
                section.section_summary = digest.summary
                self._update_citation_ledger(synthesis, section)
                await self._agent_run(
                    synthesis,
                    "thesis_writer_global_revision",
                    round_number,
                    section,
                    {"draft_markdown": revised},
                )
            synthesis.global_summary = await self._rebuild_global_summary(synthesis)
            await self._db.commit()

    async def _global_review(
        self,
        synthesis: SynthesisSession,
        manuscript_selection: dict[str, Any],
    ) -> GlobalReview:
        request = StructuredGenerationRequest(
            system_instruction=(
                "You are the Thesis Review Agent performing final global review. Check chapter "
                "logic, semantic continuity, terminology, unsupported citations, contradiction, "
                "duplication and whether conclusions follow from preceding sections. Identify only "
                "material issues and affected section keys."
            ),
            user_content=json.dumps(
                {
                    "topic": synthesis.topic,
                    "outline": synthesis.global_outline,
                    "global_summary": synthesis.global_summary,
                    "glossary": synthesis.glossary,
                    "manuscript_selection": manuscript_selection,
                    "known_evidence_ids": list(self._candidates),
                },
                ensure_ascii=False,
            ),
            response_schema=GlobalReview.model_json_schema(),
            operation=f"synthesis.global_review.{synthesis.current_round}",
            depth=cast(Literal["quick", "balanced", "deep", "max"], synthesis.model_depth),
            max_context_tokens=synthesis.max_context_tokens,
            max_output_tokens=output_token_budget(
                synthesis.max_context_tokens,
                cap=12288,
            ),
        )
        try:
            raw = await self._model.generate_structured(request)
            review = GlobalReview.model_validate(raw)
        except (ModelResponseError, ValidationError) as exc:
            review = GlobalReview(
                verdict="approved",
                overall_assessment="全稿审查模型不可用，保留章节级审查后的版本。",
            )
            await self._agent_run(
                synthesis,
                "global_reviewer_fallback",
                synthesis.current_round,
                None,
                review.model_dump(mode="json"),
                error=str(exc)[:1000],
            )
        else:
            await self._agent_run(
                synthesis,
                "global_reviewer",
                synthesis.current_round,
                None,
                review.model_dump(mode="json"),
            )
        return review

    async def _assemble(
        self,
        synthesis: SynthesisSession,
        *,
        include_references: bool = True,
    ) -> str:
        sections = await self._sections(synthesis.id)
        body = "\n\n".join(
            value.draft_markdown.strip() for value in sections if value.draft_markdown
        )
        lines = [f"# {synthesis.title}", "", body]
        if include_references:
            evidence_ids = list(
                dict.fromkeys(
                    evidence_id
                    for section in sections
                    for evidence_id in section.evidence_ids
                )
            )
            citations = await citation_map_for_candidates(
                self._db,
                list(self._candidates.values()),
            )
            references = reference_markdown(evidence_ids, citations)
            if references:
                lines.extend(["", references])
            sources = await self._sources(synthesis.id)
            used_sources = {
                source_id for section in sections for source_id in section.source_ids
            }
            if used_sources:
                lines.extend(["", "## 本地资料来源", ""])
                by_id = {str(value.id): value for value in sources}
                lines.extend(
                    f"- [SRC:{source_id}] {by_id[source_id].filename}"
                    for source_id in used_sources
                    if source_id in by_id
                )
        return "\n".join(lines).strip() + "\n"

    async def _save_review(
        self,
        synthesis: SynthesisSession,
        section: SynthesisSection,
        round_number: int,
        review: SectionReview,
    ) -> None:
        self._db.add(
            SynthesisReviewRound(
                session_id=synthesis.id,
                section_id=section.id,
                round_number=round_number,
                scope="section",
                verdict=review.verdict,
                feedback=review.model_dump(mode="json"),
                affected_section_keys=[section.section_key],
                status="completed",
            )
        )
        await self._db.flush()

    async def _agent_run(
        self,
        synthesis: SynthesisSession,
        agent_name: str,
        round_number: int,
        section: SynthesisSection | None,
        output: dict[str, Any],
        *,
        error: str | None = None,
    ) -> None:
        serialized = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
        self._db.add(
            SynthesisAgentRun(
                session_id=synthesis.id,
                section_id=section.id if section else None,
                round_number=round_number,
                agent_name=agent_name,
                input_hash=content_hash(serialized),
                output=output,
                status="failed" if error else "succeeded",
                error=error,
            )
        )
        await self._db.flush()

    async def _record_usage(self, project_id: UUID) -> None:
        await UsageService(self._db).record_model_events(
            project_id=project_id,
            events=drain_model_usage(self._model),
        )
