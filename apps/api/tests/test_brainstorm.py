import re
from collections.abc import AsyncIterator, Coroutine, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from science_buddy.api import brainstorm as brainstorm_api
from science_buddy.config import Settings
from science_buddy.domain.contracts import RetrievalCandidate
from science_buddy.domain.enums import JobStatus
from science_buddy.domain.providers import (
    LiteratureRecord,
    StructuredGenerationRequest,
    TextGenerationRequest,
)
from science_buddy.infrastructure.models import (
    Base,
    BrainstormAgentRun,
    BrainstormLiterature,
    BrainstormMessage,
    BrainstormSession,
    BrainstormVersion,
    CollectionPaper,
    Job,
    Paper,
    PaperTag,
    Project,
    ProjectPaper,
    RagCollection,
    Tag,
)
from science_buddy.main import app
from science_buddy.services import research_literature
from science_buddy.services.background_tasks import recover_interrupted_background_jobs
from science_buddy.services.brainstorm import (
    BrainstormOrchestrator,
    _agent_output_budget,
    _draft_refinement_limit,
    _retry_output_budget,
    _supplement_queries,
    safety_mode,
    sanitize_mermaid,
)
from science_buddy.services.brainstorm_prompts import (
    BACKGROUND_LITERATURE_PROMPT,
    COMMON_GUARDRAILS,
    COORDINATOR_PROMPT,
    EXPERIMENT_DESIGN_PROMPT,
    INNOVATION_CRITIC_PROMPT,
    METHOD_AGENT_PROMPT,
    ORGANIZER_SYSTEM_PROMPT,
    PROMPT_VERSION,
    REFINER_SUBAGENT_PROMPT,
    REVIEWER_SUBAGENT_PROMPT,
    SCIENTIFIC_QUESTION_PROMPT,
)
from science_buddy.services.brainstorm_sessions import BrainstormSessionService
from science_buddy.services.brainstorm_types import (
    CoordinatorOutput,
    CriticAgentOutput,
    ExperimentAgentOutput,
    ExperimentWorkPackage,
    MaterialCandidate,
    MethodAgentOutput,
    QuestionAgentOutput,
    ScientificSubQuestion,
)
from science_buddy.services.models import ModelResponseError
from science_buddy.services.retrieval_cache import RetrievalExecution
from science_buddy.services.tags import TagService


class SequentialModel:
    name = "test-model"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses

    async def generate_structured(
        self, _request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        return self.responses.pop(0)

    async def stream_text(
        self, _system: str, _messages: Sequence[str]
    ) -> AsyncIterator[str]:
        yield ""

    async def generate_text(self, request: TextGenerationRequest) -> str:
        return "自由文本草稿"


class FailingModel:
    name = "failing-model"

    async def generate_structured(
        self, _request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        raise RuntimeError("Model timeout while generating structured output")

    async def stream_text(
        self, _system: str, _messages: Sequence[str]
    ) -> AsyncIterator[str]:
        yield ""

    async def generate_text(self, _request: TextGenerationRequest) -> str:
        raise RuntimeError("Model timeout while generating text")


class InvalidJsonThenValidModel(SequentialModel):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(responses)
        self.calls = 0

    async def generate_structured(
        self, _request: StructuredGenerationRequest
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= 2:
            raise ModelResponseError(
                "The model did not return valid JSON or another supported structured object "
                "(characters=100, finish_reason=length)"
            )
        return self.responses.pop(0)


class MissingFieldsModel(SequentialModel):
    async def generate_structured(
        self,
        _request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        return {"evidence_ids": []}


class MissingFieldsThenValidModel(SequentialModel):
    def __init__(self, valid: dict[str, Any]) -> None:
        super().__init__([])
        self.valid = valid
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        self.requests.append(request)
        return {"evidence_ids": []} if len(self.requests) == 1 else self.valid


class DraftLoopModel(SequentialModel):
    def __init__(self, structured: dict[str, Any]) -> None:
        super().__init__([])
        self.structured = structured
        self.text_requests: list[TextGenerationRequest] = []
        self.structured_requests: list[StructuredGenerationRequest] = []

    async def generate_text(self, request: TextGenerationRequest) -> str:
        self.text_requests.append(request)
        if request.operation.endswith(".draft"):
            return "# 初稿\n假设与主实验。"
        if ".review." in request.operation:
            return (
                "APPROVED"
                if request.operation.endswith(".2")
                else "- 补充正交验证与阴性结果决策门"
            )
        return "# 扩充稿\n假设、主实验、正交验证、阴性结果解释和复现归档。" + ("细节" * 80)

    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        self.structured_requests.append(request)
        return self.structured


class DraftThenBrokenStructureModel(DraftLoopModel):
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, Any]:
        self.structured_requests.append(request)
        raise ModelResponseError(
            "The model did not return valid JSON or another supported structured object"
        )


def exploration_responses(
    *,
    pubmed_queries: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    cited = evidence_ids or []
    return [
        {
            "scientific_question": "Does marker X stratify outcome?",
            "hypothesis": "Marker X is associated with outcome.",
            "predictions": ["Direction differs by subgroup"],
            "innovation_rationale": ["Tests a defined subgroup"],
            "feasibility": ["Archived samples available"],
            "assumptions": [],
            "evidence_ids": [],
            "evidence_gaps": ["Local evidence is limited"],
            "pubmed_queries": pubmed_queries or [],
            "questions_to_user": ["Which cohort is available?"],
        },
        {
            "design_summary": "A bounded observational design.",
            "objectives": ["Estimate association"],
            "work_packages": [],
            "technical_route_mermaid": "flowchart TD\nA[Cohort] --> B[Analysis]",
            "materials": [],
            "analysis_plan": ["Pre-specified model"],
            "reproducibility_plan": ["Blinded analysis"],
            "evidence_gaps": [],
            "safety_flags": [],
        },
        {
            "method_overview": "展开每个工作包的方法、质控和判定标准。",
            "modules": [],
            "cross_module_timeline": [],
            "reproducibility_checklist": ["保留原始数据和分析版本"],
            "evidence_gaps": [],
            "safety_flags": [],
            "evidence_ids": cited,
        },
        {
            "strengths": ["Falsifiable"],
            "innovation_assessment": ["Moderate novelty"],
            "limitations": ["Observational confounding"],
            "alternative_explanations": ["Treatment selection"],
            "biases": ["Selection bias"],
            "feasibility_risks": ["Sample size"],
            "fatal_flaws": [],
            "safety_flags": [],
            "questions_to_user": ["What is the sample size?"],
            "evidence_ids": cited,
        },
        {
            "title": "Marker exploration",
            "response_markdown": "## Draft\nEvidence remains traceable.",
            "technical_route_mermaid": "flowchart TD\nA[Cohort] --> B[Analysis]",
            "confirmation_questions": ["Confirm the cohort?"],
            "safety_flags": [],
            "evidence_ids": cited,
            "revised_document_markdown": None,
            "change_log": [],
        },
    ]


async def make_database(path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql(
            "CREATE TRIGGER brainstorm_original_no_update BEFORE UPDATE ON "
            "brainstorm_versions WHEN OLD.kind = 'original' BEGIN "
            "SELECT RAISE(ABORT, 'original brainstorm version is immutable'); END"
        )
    return engine, sessions


def test_material_model_rejects_unsupported_catalog_number() -> None:
    with pytest.raises(ValueError, match="require supplied evidence"):
        MaterialCandidate(
            name="Candidate kit",
            category="assay",
            specification="Validated assay format",
            manufacturer="Vendor",
            catalog_model="CAT-001",
            verification_status="limited_evidence",
            evidence_ids=[],
            verification_note="Needs verification",
        )


def test_mermaid_and_safety_guards() -> None:
    assert sanitize_mermaid("```mermaid\nflowchart TD\nA[Test] --> B[Readout]\n```")
    with pytest.raises(ValueError, match="Interactive Mermaid"):
        sanitize_mermaid("flowchart TD\nA --> B\nclick A href 'https://example.org'")
    mode, markers = safety_mode("Planning BSL-3 pathogen work")
    assert mode == "restricted"
    assert "bsl-3" in markers
    assert safety_mode("正规小鼠实验与常规人体研究")[0] == "standard"


def test_prompts_assume_standard_compliance_and_require_complete_workflow() -> None:
    assert "Assume ordinary institutional compliance" in COMMON_GUARDRAILS
    assert "do not repeat" in COMMON_GUARDRAILS
    assert "falsifiable question" in COMMON_GUARDRAILS
    assert "Choose the organization" in COMMON_GUARDRAILS
    assert "actual problem" in EXPERIMENT_DESIGN_PROMPT
    assert "avoid boilerplate" in METHOD_AGENT_PROMPT
    assert "start-to-finish" in COORDINATOR_PROMPT
    assert "Do not ask routine compliance questions" in COORDINATOR_PROMPT
    assert "8-14 nodes" not in EXPERIMENT_DESIGN_PROMPT
    assert "IRB/IACUC/IBC" not in COMMON_GUARDRAILS
    combined_prompts = "\n".join(
        (
            SCIENTIFIC_QUESTION_PROMPT,
            EXPERIMENT_DESIGN_PROMPT,
            METHOD_AGENT_PROMPT,
            INNOVATION_CRITIC_PROMPT,
            COORDINATOR_PROMPT,
            REVIEWER_SUBAGENT_PROMPT,
            REFINER_SUBAGENT_PROMPT,
            ORGANIZER_SYSTEM_PROMPT,
        )
    )
    assert not re.search(r"\b(?:WP|M)\s*[-_]?\d+\b", combined_prompts)
    assert "2~3个可证伪子科学问题" in SCIENTIFIC_QUESTION_PROMPT
    assert "Three-Tier Material Guard" in EXPERIMENT_DESIGN_PROMPT
    assert "Coordinator 独占" in EXPERIMENT_DESIGN_PROMPT
    assert "多层因果树技术路线" in COORDINATOR_PROMPT


def test_coordinator_can_complete_without_forced_confirmation_question() -> None:
    output = CoordinatorOutput(
        title="完整方案",
        response_markdown="从假设到复现的完整流程。",
        confirmation_questions=[],
    )

    assert output.confirmation_questions == []
    assert output.proposal_background == ""
    assert output.scientific_question_and_hypothesis == ""


def test_prompt_v6_reviewer_and_organizer_specifications() -> None:
    assert PROMPT_VERSION == "brainstorm-agents-v6-causal-proposal"
    assert "实验硬伤 4 维拦截清单" in REVIEWER_SUBAGENT_PROMPT
    assert "标准开题报告 5 核心要素结构审计" in REVIEWER_SUBAGENT_PROMPT
    assert "EXACTLY 'APPROVED'" in REVIEWER_SUBAGENT_PROMPT
    assert "No Condensation" in ORGANIZER_SYSTEM_PROMPT
    assert "Lossless" in ORGANIZER_SYSTEM_PROMPT or "lossless" in ORGANIZER_SYSTEM_PROMPT
    assert (
        "Background Scoping" in BACKGROUND_LITERATURE_PROMPT
        or "Literature Intelligence" in BACKGROUND_LITERATURE_PROMPT
    )
    assert "Refine and expand" in REFINER_SUBAGENT_PROMPT


def test_material_candidate_supports_recommended_and_web_matched() -> None:
    candidate = MaterialCandidate(
        name="Anti-Flag M2 magnetic beads",
        category="antibody",
        specification="Binding capacity > 0.6 mg/mL",
        manufacturer="Sigma-Aldrich",
        catalog_model="M8823",
        verification_status="candidate_recommended",
        evidence_ids=[],
        verification_note="Expert recommended specification",
    )
    assert candidate.verification_status == "candidate_recommended"
    assert candidate.manufacturer == "Sigma-Aldrich"

    web_matched = MaterialCandidate(
        name="Cas9 Protein",
        category="protein",
        specification="High fidelity NLS-Cas9",
        manufacturer="NEB",
        catalog_model="M0646T",
        verification_status="web_matched",
        web_source_url="https://www.neb.com/products/m0646",
        evidence_ids=[],
        verification_note="Matched from vendor query",
    )
    assert web_matched.verification_status == "web_matched"
    assert web_matched.web_source_url == "https://www.neb.com/products/m0646"


def test_build_research_ledger_formats_hierarchical_tags() -> None:
    question = QuestionAgentOutput(
        overarching_question="受体选择压力如何塑造噬菌体宿主范围？",
        scientific_question="噬菌体尾部蛋白如何突变？",
        hypothesis="受体结合域发生点突变扩展宿主范围。",
        sub_questions=[
            ScientificSubQuestion(
                title="序列因果",
                hypothesis="H1：受体结合域突变改变吸附；H0：突变与吸附无关。",
                prediction="突变株吸附率相对对照提高至少 20%。",
            ),
            ScientificSubQuestion(
                title="结构因果",
                hypothesis="H1：结构界面重排驱动宿主扩展；H0：界面不变。",
                prediction="AlphaFold3 界面置信度与宿主范围变化同向。",
            ),
        ],
        predictions=["突变株侵染率提高"],
        evidence_gaps=["局部结构未解析"],
    )
    experiment = ExperimentAgentOutput(
        design_summary="受体压力选择实验设计",
        objectives=["验证突变"],
        work_packages=[
            ExperimentWorkPackage(
                title="受体压力筛选",
                purpose="获取抗性突变株",
                approach="梯度共培养",
                controls=["野生型对照"],
                endpoints=["空斑形成率"],
                decision_point="突变率>5%进入测序",
            )
        ],
        technical_route_mermaid="flowchart TD\nA --> B",
    )
    ledger = BrainstormOrchestrator._build_research_ledger(
        primary=question,
        experiment=experiment,
    )
    assert "<research_ledger version=\"5.0\">" in ledger
    assert "<hypothesis_core>" in ledger
    assert (
        "<overarching_question>受体选择压力如何塑造噬菌体宿主范围？"
        "</overarching_question>"
    ) in ledger
    assert "<question>噬菌体尾部蛋白如何突变？</question>" in ledger
    assert "<sub_questions>" in ledger
    assert "<title>序列因果</title>" in ledger
    assert "<prediction>突变株吸附率相对对照提高至少 20%。</prediction>" in ledger
    assert "<experiment_framework>" in ledger
    assert "<causal_nodes>" in ledger
    assert "<controls><control>野生型对照</control></controls>" in ledger
    assert "<decision>突变率&gt;5%进入测序</decision>" in ledger
    assert "WP1" not in ledger


def test_question_output_supports_new_framework_and_legacy_aliases() -> None:
    modern = QuestionAgentOutput.model_validate(
        {
            "overarching_question": "免疫生态位如何决定治疗响应？",
            "sub_questions": [
                {
                    "title": "细胞因果",
                    "hypothesis": "H1：细胞状态决定响应；H0：两者无关。",
                    "prediction": "响应组效应量绝对值大于 0.5。",
                },
                {
                    "title": "空间因果",
                    "hypothesis": "H1：空间邻近增强响应；H0：空间结构无影响。",
                    "prediction": "邻近指数与响应显著相关。",
                },
            ],
            "landscape_and_trends": "已有研究与前沿趋势。",
            "theoretical_significance": "检验机制边界。",
            "application_value": "支持非临床方法转化。",
        }
    )
    legacy = QuestionAgentOutput(
        scientific_question="旧版问题",
        hypothesis="旧版假说",
        background_summary="旧版背景",
        significance="旧版意义",
    )

    assert modern.scientific_question == modern.overarching_question
    assert len(modern.sub_questions) == 2
    assert legacy.overarching_question == "旧版问题"
    assert legacy.landscape_and_trends == "旧版背景"
    assert legacy.theoretical_significance == "旧版意义"


def test_sub_questions_leniently_normalize_common_model_shapes() -> None:
    output = QuestionAgentOutput.model_validate(
        {
            "overarching_question": "什么机制决定表型？",
            "sub_questions": [
                {
                    "question": "候选因子是否必要？",
                    "h1": "敲低改变表型",
                    "h0": "敲低不改变表型",
                    "quantitative_prediction": "效应量绝对值大于 0.5",
                },
                "候选因子是否充分？",
                None,
            ],
        }
    )

    assert output.sub_questions[0].title == "候选因子是否必要？"
    assert output.sub_questions[0].hypothesis == "H1：敲低改变表型；H0：敲低不改变表型"
    assert output.sub_questions[0].prediction == "效应量绝对值大于 0.5"
    assert output.sub_questions[1].title == "候选因子是否充分？"


def test_coordinator_confirmation_questions_are_lenient_and_clean() -> None:
    output = CoordinatorOutput(
        title="方案",
        response_markdown="正文",
        confirmation_questions="主要终点？\n\n主要终点？\n样本量？",
    )

    assert output.confirmation_questions == ["主要终点？", "样本量？"]


def test_mermaid_sanitizer_rewrites_robotic_stage_codes() -> None:
    cleaned = sanitize_mermaid(
        "flowchart TD\nWP1[WP1 机制筛选] --> M2[M2 功能验证]"
    )

    assert cleaned is not None
    assert "WP1" not in cleaned
    assert "M2" not in cleaned
    assert "研究阶段1" in cleaned
    assert "方法阶段2" in cleaned


def test_plan_internal_prompt_is_not_used_as_external_literature_query() -> None:
    internal_prompt = (
        "Generate the final technical route and experimental proposal for this "
        "selected Plan direction."
    )

    queries = _supplement_queries(
        internal_prompt,
        ["Klebsiella phage tail fiber evolution"],
        ["phage defense Tai Bil adsorption"],
    )

    assert internal_prompt not in queries
    assert queries == [
        "Klebsiella phage tail fiber evolution",
        "phage defense Tai Bil adsorption",
    ]


def test_agent_output_budgets_prioritize_long_experiment_and_method_outputs() -> None:
    assert _agent_output_budget("scientific_question", "max") == 12288
    assert _agent_output_budget("experiment_design", "max") == 49152
    assert _agent_output_budget("method_agent", "max") == 65536
    assert _agent_output_budget("coordinator", "max") == 32768
    assert _retry_output_budget(49152, "deepseek") == 98304
    assert _retry_output_budget(65536, "deepseek") == 131072
    assert _draft_refinement_limit("experiment_design", "max") == 2
    assert _draft_refinement_limit("method_agent", "deep") == 1
    assert _draft_refinement_limit("coordinator", "max") == 0
    assert _draft_refinement_limit("scientific_question", "max") == -1
    assert _draft_refinement_limit("method_agent", "quick") == -1


def test_explicit_fake_evidence_id_is_rejected() -> None:
    output = QuestionAgentOutput(
        scientific_question="Question",
        hypothesis="Hypothesis",
        evidence_ids=["invented-evidence-id"],
    )
    with pytest.raises(ValueError, match="outside the current candidate set"):
        BrainstormOrchestrator._validate_evidence_ids(output, [])


def test_evidence_guard_removes_unknown_ids_recursively_before_strict_validation() -> None:
    allowed_id = "ev1.allowed"
    unknown_id = "ev1.discovery-only"
    output = ExperimentAgentOutput(
        design_summary=f"候选推断 [{unknown_id}]，已知证据 [{allowed_id}]。",
        technical_route_mermaid="flowchart TD\nA --> B",
        work_packages=[
            {
                "title": "验证",
                "purpose": "区分候选机制",
                "approach": "受控比较",
                "evidence_ids": [allowed_id, unknown_id],
            }
        ],
        evidence_gaps=[],
    )
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        evidence_id=allowed_id,
        text="Current-turn evidence",
        score=1.0,
        source_locator={},
    )

    removed = BrainstormOrchestrator._sanitize_evidence_ids(output, [candidate])
    BrainstormOrchestrator._validate_evidence_ids(output, [candidate])

    assert removed == {unknown_id}
    assert unknown_id not in output.design_summary
    assert allowed_id in output.design_summary
    assert output.work_packages[0].evidence_ids == [allowed_id]


@pytest.mark.asyncio
async def test_evidence_guard_persists_audit_and_marks_evidence_gap(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "evidence-guard.db")
    allowed_id = "ev1.allowed"
    unknown_id = "ev1.discovery-only"
    async with sessions() as session:
        project = Project(name="Evidence guard project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Evidence guard",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )
        output = QuestionAgentOutput(
            scientific_question="可否验证候选机制？",
            hypothesis=f"候选机制可能成立 [{unknown_id}]",
            evidence_ids=[allowed_id, unknown_id],
        )
        candidate = RetrievalCandidate(
            chunk_id=uuid4(),
            evidence_id=allowed_id,
            text="Current-turn evidence",
            score=1.0,
            source_locator={},
        )

        async def empty_candidates(*_args: object) -> list[RetrievalCandidate]:
            return []

        orchestrator = BrainstormOrchestrator(
            session,
            model=SequentialModel([]),
            settings=Settings(_env_file=None),
            retrieve_evidence=empty_candidates,
            supplement_literature=empty_candidates,
        )

        await orchestrator._guard_evidence_ids(
            brainstorm,
            1,
            "scientific_question",
            output,
            [candidate],
        )
        await session.commit()
        guard = await session.scalar(
            select(BrainstormAgentRun).where(
                BrainstormAgentRun.session_id == brainstorm.id,
                BrainstormAgentRun.agent_name == "evidence_reference_guard",
            )
        )

    assert output.evidence_ids == [allowed_id]
    assert unknown_id not in output.hypothesis
    assert any("不得视为已获证据支持" in gap for gap in output.evidence_gaps)
    assert guard is not None
    assert guard.output["removed_evidence_ids"] == [unknown_id]
    await engine.dispose()  # type: ignore[attr-defined]


def test_unsupported_material_model_is_removed_without_failing_turn() -> None:
    evidence_id = "ev1.test"
    output = ExperimentAgentOutput(
        design_summary="Design",
        technical_route_mermaid="flowchart TD\nA --> B",
        materials=[
            MaterialCandidate(
                name="Assay kit",
                category="assay",
                specification="Candidate format",
                manufacturer="Vendor X",
                catalog_model="MODEL-42",
                verification_status="direct_evidence",
                evidence_ids=[evidence_id],
                verification_note="Cited candidate",
            )
        ],
    )
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        evidence_id=evidence_id,
        text="The study used a commercial assay without naming a product.",
        score=1.0,
        source_locator={},
    )
    warnings = BrainstormOrchestrator._sanitize_material_candidates(output, [candidate])

    assert len(warnings) == 1
    assert output.materials[0].manufacturer is None
    assert output.materials[0].catalog_model is None
    assert output.materials[0].verification_status == "limited_evidence"
    assert "证据安全校验已自动移除" in output.materials[0].verification_note


def test_material_version_format_variants_are_supported() -> None:
    evidence_id = "ev1.version"
    output = ExperimentAgentOutput(
        design_summary="Design",
        technical_route_mermaid="flowchart TD\nA --> B",
        materials=[
            MaterialCandidate(
                name="Assembly workflow",
                category="software",
                specification="Genome assembly",
                catalog_model="Unicycler 0.5.0",
                verification_status="direct_evidence",
                evidence_ids=[evidence_id],
                verification_note="Cited software version",
            )
        ],
    )
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        evidence_id=evidence_id,
        text="Genomes were reassembled using Unicycler (version 0.5.0).",
        score=1.0,
        source_locator={},
    )

    warnings = BrainstormOrchestrator._sanitize_material_candidates(output, [candidate])

    assert warnings == []
    assert output.materials[0].catalog_model == "Unicycler 0.5.0"
    assert output.materials[0].verification_status == "direct_evidence"


def test_deterministic_coordinator_preserves_validated_specialist_outputs() -> None:
    primary = QuestionAgentOutput(
        scientific_question="标志物是否与结局独立相关？",
        hypothesis="校正混杂后仍存在关联。",
        evidence_ids=["ev1.paper"],
        questions_to_user=["请确认主要结局。"],
    )
    experiment = ExperimentAgentOutput(
        design_summary="采用去标识化回顾性队列。",
        objectives=["估计校正效应量"],
        work_packages=[],
        technical_route_mermaid="flowchart TD\nA[队列] --> B[分析]",
        safety_flags=["需要数据治理审批"],
    )
    method = MethodAgentOutput(
        method_overview="展开纳排、质控和分析。",
        modules=[],
        evidence_ids=["ev1.paper"],
    )
    critic = CriticAgentOutput(
        limitations=["存在残余混杂"],
        questions_to_user=["是否有验证队列？"],
    )

    result = BrainstormOrchestrator._deterministic_coordinator(
        brainstorm_session=BrainstormSession(mode="exploration"),
        primary=primary,
        experiment=experiment,
        method=method,
        critic=critic,
        latest_content=None,
    )

    assert "标志物是否与结局独立相关" in result.response_markdown
    assert "采用去标识化回顾性队列" in result.response_markdown
    assert "## 一、选题背景、立项依据与国内外研究进展" in result.response_markdown
    assert "## 二、核心大科学问题与子科学问题假说链条" in result.response_markdown
    assert "## 三、理论科学意义与应用价值" in result.response_markdown
    assert "## 四、总体实验架构与技术路线" in result.response_markdown
    assert "## 五、课题创新性、局限性审判与待确认决策" in result.response_markdown
    assert result.technical_route_mermaid == experiment.technical_route_mermaid
    assert result.evidence_ids == ["ev1.paper"]
    assert any("请确认主要结局" in value for value in result.confirmation_questions)


def test_validated_method_appendix_uses_natural_sections_and_stitches_once() -> None:
    experiment = ExperimentAgentOutput(
        design_summary="对称验证两个子问题。",
        work_packages=[
            ExperimentWorkPackage(
                title="机制筛选",
                purpose="筛选候选机制",
                approach="扰动后测量",
            )
        ],
    )
    method = MethodAgentOutput(
        method_overview="按实验台执行顺序展开。",
        modules=[
            {
                "title": "功能验证",
                "objective": "验证因果性",
                "principle": "受控扰动与救援",
                "staged_procedure": ["在预实验确定的条件下完成扰动。"],
            }
        ],
    )

    appendix = BrainstormOrchestrator._validated_method_appendix(experiment, method)
    stitched = BrainstormOrchestrator._stitch_method_appendix("## 正文\n内容", appendix)
    stitched_twice = BrainstormOrchestrator._stitch_method_appendix(stitched, appendix)
    replaced = BrainstormOrchestrator._stitch_method_appendix(
        "## 正文\n内容\n\n## 附录：具体实验方法规程（SOP）\n旧附录"
        "\n\n## 参考文献\n1. 保留来源",
        appendix,
    )

    assert appendix.startswith("## 附录：具体实验方法规程（SOP）")
    assert "### 实验阶段 1：机制筛选" in appendix
    assert "### 方法阶段 1：功能验证" in appendix
    assert "WP1" not in appendix
    assert "M1" not in appendix
    assert stitched_twice == stitched
    assert stitched.count("## 附录：具体实验方法规程（SOP）") == 1
    assert "旧附录" not in replaced
    assert "## 参考文献\n1. 保留来源" in replaced


@pytest.mark.asyncio
async def test_refinement_versions_never_overwrite_original(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "versioning.db")
    async with sessions() as session:
        project = Project(name="Version project")
        session.add(project)
        await session.commit()
        service = BrainstormSessionService(session)
        brainstorm = await service.create(
            project_id=project.id,
            mode="refinement",
            title="Improve proposal",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            model_depth="quick",
        )
        original = await service.add_version(
            session_id=brainstorm.id,
            kind="original",
            content="Immutable original proposal",
            parent_version_id=None,
        )
        original_id = original.id
        await session.commit()
        draft = await service.add_version(
            session_id=brainstorm.id,
            kind="draft",
            content="Improved proposal",
            parent_version_id=original.id,
        )
        draft_parent_id = draft.parent_version_id
        await session.commit()
        with pytest.raises(DatabaseError, match="original brainstorm version is immutable"):
            await session.execute(
                text("UPDATE brainstorm_versions SET content='overwritten' WHERE id=:id"),
                    {"id": original_id.hex},
            )
            await session.commit()
        await session.rollback()
        stored_original = await session.get(BrainstormVersion, original_id)

    assert stored_original is not None
    assert stored_original.content == "Immutable original proposal"
    assert draft_parent_id == original_id
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_agent_missing_fields_uses_deterministic_fallback(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "missing-fields-fallback.db")
    async with sessions() as session:
        project = Project(name="Missing fields project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Missing fields fallback",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )

        async def empty_candidates(*_args: object) -> list[RetrievalCandidate]:
            return []

        orchestrator = BrainstormOrchestrator(
            session,
            model=MissingFieldsModel([]),
            settings=Settings(_env_file=None),
            retrieve_evidence=empty_candidates,
            supplement_literature=empty_candidates,
        )

        output = await orchestrator._call_agent_with_fallback(
            brainstorm,
            1,
            "scientific_question",
            "Return a scientific question.",
            {"user_message": "研究噬菌体共进化", "evidence": []},
            QuestionAgentOutput,
        )
        fallback_run = await session.scalar(
            select(BrainstormAgentRun).where(
                BrainstormAgentRun.session_id == brainstorm.id,
                BrainstormAgentRun.agent_name == "scientific_question_fallback",
            )
        )

    assert output.scientific_question
    assert output.evidence_gaps
    assert fallback_run is not None
    assert str(fallback_run.error).startswith("invalid_structure_fallback:")
    assert orchestrator._fallback_agents == {"scientific_question"}
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_agent_missing_fields_gets_high_budget_repair_before_fallback(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "structure-repair.db")
    valid = exploration_responses()[0]
    model = MissingFieldsThenValidModel(valid)
    async with sessions() as session:
        project = Project(name="Structure repair project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Structure repair",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            model_depth="max",
        )

        async def empty_candidates(*_args: object) -> list[RetrievalCandidate]:
            return []

        orchestrator = BrainstormOrchestrator(
            session,
            model=model,
            settings=Settings(_env_file=None),
            retrieve_evidence=empty_candidates,
            supplement_literature=empty_candidates,
        )
        output = await orchestrator._call_agent_with_fallback(
            brainstorm,
            1,
            "scientific_question",
            "Return a scientific question.",
            {"user_message": "研究噬菌体共进化", "evidence": []},
            QuestionAgentOutput,
        )

    assert output.scientific_question == valid["scientific_question"]
    assert [request.operation for request in model.requests] == [
        "brainstorm.scientific_question",
        "brainstorm.scientific_question.structure_retry",
    ]
    assert model.requests[0].max_output_tokens == 12288
    assert model.requests[1].max_output_tokens == 24576
    assert orchestrator._fallback_agents == set()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_max_agent_uses_bounded_draft_review_refine_then_structure(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "draft-loop.db")
    structured = exploration_responses()[1]
    model = DraftLoopModel(structured)
    async with sessions() as session:
        project = Project(name="Draft loop project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Draft loop",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            model_depth="max",
        )

        async def empty_candidates(*_args: object) -> list[RetrievalCandidate]:
            return []

        orchestrator = BrainstormOrchestrator(
            session,
            model=model,
            settings=Settings(_env_file=None),
            retrieve_evidence=empty_candidates,
            supplement_literature=empty_candidates,
        )
        output = await orchestrator._call_agent_with_fallback(
            brainstorm,
            1,
            "experiment_design",
            "Design the experiment.",
            {"user_message": "研究噬菌体共进化", "evidence": []},
            ExperimentAgentOutput,
        )
        runs = list(
            (
                await session.scalars(
                    select(BrainstormAgentRun).where(
                        BrainstormAgentRun.session_id == brainstorm.id
                    )
                )
            ).all()
        )

    assert output.design_summary == structured["design_summary"]
    operations = [request.operation for request in model.text_requests]
    assert operations == [
        "brainstorm.experiment_design.draft",
        "brainstorm.experiment_design.review.1",
        "brainstorm.experiment_design.refine.1",
        "brainstorm.experiment_design.review.2",
    ]
    assert "Required JSON Schema" not in model.text_requests[0].system_instruction
    assert "Return one JSON object" not in model.text_requests[0].system_instruction
    assert "Do not output JSON" in model.text_requests[0].system_instruction
    assert model.structured_requests[0].operation == "brainstorm.experiment_design"
    assert "<scientific_draft>" in model.structured_requests[0].user_content
    assert orchestrator._draft_loops["experiment_design"]["refinement_rounds"] == 1
    assert orchestrator._draft_loops["experiment_design"]["stop_reason"] == "reviewer_approved"
    assert {run.agent_name for run in runs} >= {
        "experiment_design_draft",
        "experiment_design_review",
        "experiment_design_refine",
        "experiment_design",
    }
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_broken_structure_preserves_free_scientific_draft(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "draft-preserved.db")
    model = DraftThenBrokenStructureModel({})
    async with sessions() as session:
        project = Project(name="Draft preserved project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Draft preserved",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            model_depth="balanced",
        )

        async def empty_candidates(*_args: object) -> list[RetrievalCandidate]:
            return []

        orchestrator = BrainstormOrchestrator(
            session,
            model=model,
            settings=Settings(_env_file=None),
            retrieve_evidence=empty_candidates,
            supplement_literature=empty_candidates,
        )
        output = await orchestrator._call_agent_with_fallback(
            brainstorm,
            1,
            "experiment_design",
            "Design the experiment.",
            {"user_message": "研究噬菌体共进化", "evidence": []},
            ExperimentAgentOutput,
        )

    assert output.design_summary.startswith("# 扩充稿")
    assert "experiment_design" in orchestrator._fallback_agents
    assert any("自由科学草稿已保留" in gap for gap in output.evidence_gaps)
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_restore_creates_non_destructive_version_branch(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "restore.db")
    async with sessions() as session:
        project = Project(name="Restore project")
        session.add(project)
        await session.commit()
        service = BrainstormSessionService(session)
        brainstorm = await service.create(
            project_id=project.id,
            mode="exploration",
            title="Restore test",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            model_depth="deep",
            agent_background="Act as a translational research team.",
        )
        first = await service.add_version(
            session_id=brainstorm.id,
            kind="draft",
            content="First proposal",
            parent_version_id=None,
        )
        await service.add_version(
            session_id=brainstorm.id,
            kind="draft",
            content="Second proposal",
            parent_version_id=first.id,
        )
        restored = await service.restore_version(
            session_id=brainstorm.id,
            version_id=first.id,
        )
        history = await service.history(brainstorm.id)

    assert [version.content for version in history.versions] == [
        "First proposal",
        "Second proposal",
        "First proposal",
    ]
    assert restored.kind == "restore"
    assert restored.parent_version_id == first.id
    assert brainstorm.model_depth == "deep"
    assert brainstorm.agent_background == "Act as a translational research team."
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_session_can_be_renamed_before_soft_delete(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "rename-session.db")
    async with sessions() as session:
        project = Project(name="Rename project")
        session.add(project)
        await session.commit()
        service = BrainstormSessionService(session)
        brainstorm = await service.create(
            project_id=project.id,
            mode="exploration",
            title="Old title",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=False,
        )
        renamed = await service.rename(brainstorm.id, "  New   research title  ")
        await service.soft_delete(brainstorm.id, "test")

        with pytest.raises(ValueError, match="does not exist"):
            await service.rename(brainstorm.id, "Hidden title")

    assert renamed.title == "New research title"
    assert renamed.deleted_at is not None
    await engine.dispose()  # type: ignore[attr-defined]


def test_two_tier_reviewer_and_refiner_prompts() -> None:
    assert "宏观生物学逻辑核验" in REVIEWER_SUBAGENT_PROMPT
    assert "实验硬伤 4 维拦截清单" in REVIEWER_SUBAGENT_PROMPT
    assert "宏观逻辑重构需求" in REVIEWER_SUBAGENT_PROMPT
    assert "宏观逻辑重构" in REFINER_SUBAGENT_PROMPT
    assert "深度参数与对照武装" in REFINER_SUBAGENT_PROMPT
    assert "No Condensation" in REFINER_SUBAGENT_PROMPT



@pytest.mark.asyncio
async def test_exploration_orchestrator_persists_agent_audit_and_version(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "orchestrator.db")
    async with sessions() as session:
        project = Project(name="Brainstorm project")
        session.add(project)
        await session.commit()
        service = BrainstormSessionService(session)
        brainstorm = await service.create(
            project_id=project.id,
            mode="exploration",
            title="Explore BRAF",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            model_depth="quick",
        )
        model = SequentialModel(
            [
                {
                    "scientific_question": "Does marker X stratify outcome?",
                    "hypothesis": "Marker X is associated with outcome.",
                    "predictions": ["Direction differs by subgroup"],
                    "innovation_rationale": ["Tests a defined subgroup"],
                    "feasibility": ["Archived samples available"],
                    "assumptions": [],
                    "evidence_ids": [],
                    "evidence_gaps": ["No local evidence"],
                    "pubmed_queries": [],
                    "questions_to_user": ["Which cohort is available?"],
                },
                {
                    "design_summary": "A bounded observational design.",
                    "objectives": ["Estimate association"],
                    "work_packages": [],
                    "technical_route_mermaid": "flowchart TD\nA[Cohort] --> B[Analysis]",
                    "materials": [
                        {
                            "name": "Validated assay",
                            "category": "assay",
                            "specification": "Institution-validated format",
                            "manufacturer": "Unsupported Vendor",
                            "catalog_model": None,
                            "verification_status": "direct_evidence",
                            "evidence_ids": ["ev1.assay"],
                            "verification_note": "Candidate vendor",
                        }
                    ],
                    "analysis_plan": ["Pre-specified model"],
                    "reproducibility_plan": ["Blinded analysis"],
                    "evidence_gaps": ["Assay choice unresolved"],
                    "safety_flags": [],
                },
                {
                    "method_overview": "展开方法模块。",
                    "modules": [],
                    "cross_module_timeline": [],
                    "reproducibility_checklist": [],
                    "evidence_gaps": [],
                    "safety_flags": [],
                    "evidence_ids": [],
                },
                {
                    "strengths": ["Falsifiable"],
                    "innovation_assessment": ["Moderate novelty"],
                    "limitations": ["Observational confounding"],
                    "alternative_explanations": ["Treatment selection"],
                    "biases": ["Selection bias"],
                    "feasibility_risks": ["Sample size"],
                    "fatal_flaws": [],
                    "safety_flags": [],
                    "questions_to_user": ["What is the sample size?"],
                    "evidence_ids": [],
                },
                {
                    "title": "BRAF exploration",
                    "response_markdown": "## Draft\nEvidence gap remains explicit.",
                    "technical_route_mermaid": "flowchart TD\nA[Cohort] --> B[Analysis]",
                    "confirmation_questions": ["Confirm the cohort?"],
                    "safety_flags": [],
                    "evidence_ids": [],
                    "revised_document_markdown": None,
                    "change_log": [],
                },
            ]
        )

        candidate = RetrievalCandidate(
            chunk_id=uuid4(),
            evidence_id="ev1.assay",
            text="The study used a validated commercial assay without naming its vendor.",
            score=1.0,
            source_locator={},
        )

        async def retrieve(_query: str, _collection_id):  # type: ignore[no-untyped-def]
            return [candidate]

        async def supplement(_queries: list[str], _turn: int):  # type: ignore[no-untyped-def]
            return []

        result = await BrainstormOrchestrator(
            session,
            model=model,
            settings=Settings(_env_file=None),
            retrieve_evidence=retrieve,
            supplement_literature=supplement,
        ).run_turn(brainstorm, user_message="Explore a marker study")
        runs = list(
            (
                await session.scalars(
                    select(BrainstormAgentRun).where(
                        BrainstormAgentRun.session_id == brainstorm.id
                    )
                )
            ).all()
        )
        versions = list(
            (
                await session.scalars(
                    select(BrainstormVersion).where(
                        BrainstormVersion.session_id == brainstorm.id
                    )
                )
            ).all()
        )

    assert result.version_number == 1
    assert len(runs) == 6
    assert {run.agent_name for run in runs} == {
        "scientific_question",
        "experiment_design",
        "material_evidence_guard",
        "method_agent",
        "innovation_critic",
        "coordinator",
    }
    guard = next(run for run in runs if run.agent_name == "material_evidence_guard")
    assert guard.output["materials"][0]["manufacturer"] is None
    assert "证据安全校验已自动移除" in guard.output["warnings"][0]
    assert (
        "## 一、选题背景、立项依据与国内外研究进展"
        in result.coordinator.response_markdown
    )
    assert (
        "## 五、课题创新性、局限性审判与待确认决策"
        in result.coordinator.response_markdown
    )
    assert "## 附录：具体实验方法规程（SOP）" in result.coordinator.response_markdown
    assert versions[0].kind == "draft"
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_invalid_question_json_uses_deterministic_fallback_and_finishes_turn(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "question-json-fallback.db")
    async with sessions() as session:
        project = Project(name="Question fallback project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Question fallback",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )
        model = InvalidJsonThenValidModel(exploration_responses()[1:])

        async def retrieve(
            _query: str, _collection_id: UUID | None
        ) -> list[RetrievalCandidate]:
            return []

        async def supplement(
            _queries: list[str], _turn: int
        ) -> list[RetrievalCandidate]:
            return []

        result = await BrainstormOrchestrator(
            session,
            model=model,
            settings=Settings(_env_file=None),
            retrieve_evidence=retrieve,
            supplement_literature=supplement,
        ).run_turn(brainstorm, user_message="测试结构化降级")
        runs = list(
            (
                await session.scalars(
                    select(BrainstormAgentRun).where(
                        BrainstormAgentRun.session_id == brainstorm.id
                    )
                )
            ).all()
        )

    assert result.version_number == 1
    assert model.calls == 6
    assert any(run.agent_name == "scientific_question_fallback" for run in runs)
    failed = next(run for run in runs if run.agent_name == "scientific_question")
    assert failed.status == "failed"
    fallback = next(
        run for run in runs if run.agent_name == "scientific_question_fallback"
    )
    assert fallback.status == "succeeded"
    assert fallback.error is not None and "finish_reason=length" in fallback.error
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_session_tags_do_not_enable_manual_override(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "session-tags.db")
    async with sessions() as session:
        project = Project(name="Tags project")
        paper = Paper(title="Reference", authors=[], publication_types=[])
        session.add_all([project, paper])
        await session.flush()
        link = ProjectPaper(project_id=project.id, paper_id=paper.id)
        session.add(link)
        await session.commit()
        tags = await TagService(session).add_session_tags(
            project.id,
            paper.id,
            ["实验参考", "会话3"],
        )
        await session.commit()

    assert {tag.name for tag in tags} == {"实验参考", "会话3"}
    assert {tag.origin for tag in tags} == {"brainstorm"}
    assert not link.tags_manually_curated
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_message_api_rejects_sessions_without_model_consent(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine, sessions = await make_database(tmp_path / "model-consent.db")
    async with sessions() as session:
        project = Project(name="Consent project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Private session",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=False,
        )
        session_id = brainstorm.id

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    def unexpected_model_build(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("The model must not be built without explicit consent")

    monkeypatch.setattr(brainstorm_api, "build_model_provider", unexpected_model_build)
    app.dependency_overrides[brainstorm_api.get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/messages",
                json={"content": "Explore a marker study"},
            )
    finally:
        app.dependency_overrides.clear()

    async with sessions() as session:
        stored = await session.get(BrainstormSession, session_id)
        messages = list(
            (
                await session.scalars(
                    select(BrainstormMessage).where(
                        BrainstormMessage.session_id == session_id
                    )
                )
            ).all()
        )

    assert response.status_code == 409
    assert "has not authorized" in response.json()["detail"]
    assert stored is not None
    assert stored.status == "active"
    assert messages == []
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_message_api_degrades_when_all_retrieval_routes_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await make_database(tmp_path / "retrieval-degraded.db")
    async with sessions() as session:
        project = Project(name="Degraded retrieval project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Degraded retrieval",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )
        session_id = brainstorm.id

    model = SequentialModel(exploration_responses())

    async def failing_retrieval(**_kwargs: Any) -> RetrievalExecution:
        raise RuntimeError("All retrieval routes failed")

    def fake_build_model(
        _client: httpx.AsyncClient,
        _settings: Settings,
    ) -> SequentialModel:
        return model

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    monkeypatch.setattr(brainstorm_api, "retrieve_with_cache", failing_retrieval)
    monkeypatch.setattr(brainstorm_api, "build_model_provider", fake_build_model)
    monkeypatch.setattr(brainstorm_api, "async_session_factory", sessions)
    app.dependency_overrides[brainstorm_api.get_session] = override_session
    app.state.cache = object()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/messages",
                json={"content": "Suggest a direction without indexed evidence"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["evidence_count"] == 0
    async with sessions() as session:
        stored = await session.get(BrainstormSession, session_id)
    assert stored is not None
    assert stored.status == "awaiting_confirmation"
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_plan_discovery_runs_after_request_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, sessions = await make_database(tmp_path / "plan-background.db")
    async with sessions() as session:
        project = Project(name="Background Plan project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Background Plan",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
            allow_web_search=False,
        )
        session_id = brainstorm.id

    model = SequentialModel(
        [
            {
                "directions": [
                    {
                        "direction_id": f"direction-{index}",
                        "title": f"方向 {index}",
                        "rationale": "可证伪且可实施。",
                    }
                    for index in range(1, 4)
                ],
                "preference_questions": [
                    {
                        "question_id": f"question-{index}",
                        "question": f"约束问题 {index}",
                        "kind": "text",
                    }
                    for index in range(1, 4)
                ],
            }
        ]
    )
    pending: list[Coroutine[Any, Any, None]] = []

    class CapturingTaskManager:
        def start(self, coroutine: Coroutine[Any, Any, None]) -> None:
            pending.append(coroutine)

    async def empty_retrieval(**_kwargs: Any) -> RetrievalExecution:
        return RetrievalExecution(candidates=[], report={}, cache_level="miss")

    async def fake_model_settings(
        _session: object,
        _settings: Settings,
    ) -> Settings:
        return Settings(_env_file=None)

    def fake_build_model(
        _client: httpx.AsyncClient,
        _settings: Settings,
    ) -> SequentialModel:
        return model

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    monkeypatch.setattr(brainstorm_api, "async_session_factory", sessions)
    monkeypatch.setattr(brainstorm_api, "retrieve_with_cache", empty_retrieval)
    monkeypatch.setattr(brainstorm_api, "resolve_model_settings", fake_model_settings)
    monkeypatch.setattr(brainstorm_api, "build_model_provider", fake_build_model)
    app.dependency_overrides[brainstorm_api.get_session] = override_session
    app.state.cache = object()
    app.state.background_tasks = CapturingTaskManager()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/plan/discover",
                json={"seed_interest": "探索宿主与微生物共进化", "max_directions": 4},
            )

        assert response.status_code == 202, response.text
        assert response.json()["status"] == "queued"
        assert len(pending) == 1
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            duplicate = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/plan/discover",
                json={"seed_interest": "探索宿主与微生物共进化", "max_directions": 4},
            )
            status_response = await client.get(
                f"/api/v1/brainstorm/sessions/{session_id}/background-job"
            )
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == response.json()["id"]
        assert len(pending) == 1
        assert status_response.status_code == 200
        assert status_response.json()["id"] == response.json()["id"]
        async with sessions() as session:
            queued_session = await session.get(BrainstormSession, session_id)
        assert queued_session is not None
        assert queued_session.status == "processing"

        await pending.pop(0)

        async with sessions() as session:
            completed_session = await session.get(BrainstormSession, session_id)
            stored_job = await session.get(Job, UUID(response.json()["id"]))
        assert completed_session is not None
        assert completed_session.status == "active"
        assert completed_session.phase == "collecting_preferences"
        assert len(completed_session.plan_snapshot["directions"]) == 3
        assert stored_job is not None
        assert stored_job.status.value == "succeeded"
    finally:
        app.dependency_overrides.clear()
        for coroutine in pending:
            coroutine.close()

    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_interrupted_plan_job_is_failed_and_session_is_unlocked(
    tmp_path: Path,
) -> None:
    engine, sessions = await make_database(tmp_path / "interrupted-plan-job.db")
    async with sessions() as session:
        project = Project(name="Interrupted Plan project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Interrupted Plan",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
            workflow="plan",
        )
        job = Job(
            kind="brainstorm_plan_discovery",
            status=JobStatus.RUNNING,
            idempotency_key=f"interrupted:{brainstorm.id}",
            payload={"session_id": str(brainstorm.id), "request": {}},
        )
        session.add(job)
        brainstorm.status = "processing"
        brainstorm.phase = "discovering_directions"
        await session.commit()
        session_id = brainstorm.id
        job_id = job.id

    recovered = await recover_interrupted_background_jobs(sessions)

    async with sessions() as session:
        stored_session = await session.get(BrainstormSession, session_id)
        stored_job = await session.get(Job, job_id)
    assert recovered == 1
    assert stored_job is not None
    assert stored_job.status == JobStatus.FAILED
    assert "API 服务" in str(stored_job.error_message)
    assert stored_session is not None
    assert stored_session.status == "active"
    assert stored_session.phase == "plan_directions"
    assert stored_session.plan_snapshot["background_job"]["status"] == "failed"
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_agent_failure_audit_survives_turn_failure(tmp_path: Path) -> None:
    engine, sessions = await make_database(tmp_path / "agent-failure.db")
    async with sessions() as session:
        project = Project(name="Failure audit project")
        session.add(project)
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Failure audit",
            collection_id=None,
            allow_pubmed_search=False,
            model_processing_allowed=True,
        )
        session_id = brainstorm.id

        async def retrieve(
            _query: str, _collection_id: UUID | None
        ) -> list[RetrievalCandidate]:
            return []

        async def supplement(
            _queries: list[str], _turn: int
        ) -> list[RetrievalCandidate]:
            return []

        with pytest.raises(RuntimeError, match="Model timeout"):
            await BrainstormOrchestrator(
                session,
                model=FailingModel(),
                settings=Settings(_env_file=None),
                retrieve_evidence=retrieve,
                supplement_literature=supplement,
                audit_session_factory=sessions,
            ).run_turn(brainstorm, user_message="Trigger the failing agent")

    async with sessions() as session:
        runs = list(
            (
                await session.scalars(
                    select(BrainstormAgentRun).where(
                        BrainstormAgentRun.session_id == session_id
                    )
                )
            ).all()
        )
        messages = list(
            (
                await session.scalars(
                    select(BrainstormMessage).where(
                        BrainstormMessage.session_id == session_id
                    )
                )
            ).all()
        )

    assert len(runs) == 1
    failed = runs[0]
    assert failed.agent_name == "scientific_question"
    assert failed.status == "failed"
    assert failed.output == {}
    assert failed.error is not None and "timeout" in failed.error.casefold()
    assert len(failed.input_hash) == 64
    assert all(character in "0123456789abcdef" for character in failed.input_hash)
    assert failed.prompt_version
    assert failed.model_provider
    assert [(message.role, message.content) for message in messages] == [
        ("user", "Trigger the failing agent")
    ]
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pubmed_supplement_tags_paper_and_keeps_initial_retrieval_scoped(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    engine, sessions = await make_database(tmp_path / "pubmed-supplement.db")
    async with sessions() as session:
        project = Project(name="Supplement project")
        selected_paper = Paper(
            title="Selected collection paper",
            authors=[],
            publication_types=["Journal Article"],
        )
        supplemental_paper = Paper(
            pmid="999001",
            title="Supplemental marker evidence",
            authors=[],
            publication_types=["Journal Article"],
        )
        session.add_all([project, selected_paper, supplemental_paper])
        await session.flush()
        session.add(ProjectPaper(project_id=project.id, paper_id=selected_paper.id))
        collection = RagCollection(project_id=project.id, name="Selected evidence")
        session.add(collection)
        await session.flush()
        session.add(
            CollectionPaper(collection_id=collection.id, paper_id=selected_paper.id)
        )
        await session.commit()
        brainstorm = await BrainstormSessionService(session).create(
            project_id=project.id,
            mode="exploration",
            title="Supplemented exploration",
            collection_id=collection.id,
            allow_pubmed_search=True,
            model_processing_allowed=True,
        )
        project_id = project.id
        session_id = brainstorm.id
        session_number = brainstorm.session_number
        collection_id = collection.id
        supplemental_paper_id = supplemental_paper.id

    evidence_id = "ev1.pubmed-supplement"
    supplemental_candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        evidence_id=evidence_id,
        text="Marker X was associated with outcome in the supplemental cohort.",
        score=1.0,
        source_locator={"pmid": "999001"},
        paper_id=supplemental_paper_id,
        role="anchor",
    )
    model = SequentialModel(
        exploration_responses(
            pubmed_queries=["marker X outcome"],
            evidence_ids=[evidence_id],
        )
    )
    observed_collection_ids: list[UUID | None] = []
    observed_pubmed_queries: list[tuple[str, int]] = []

    async def fake_retrieve_with_cache(**kwargs: Any) -> RetrievalExecution:
        current_collection_id = kwargs["collection_id"]
        observed_collection_ids.append(current_collection_id)
        candidates = [supplemental_candidate] if current_collection_id is None else []
        return RetrievalExecution(candidates=candidates, report={}, cache_level="miss")

    async def fake_pubmed_search(
        _provider: object,
        query: str,
        *,
        limit: int,
    ) -> list[LiteratureRecord]:
        observed_pubmed_queries.append((query, limit))
        return [
            LiteratureRecord(
                provider="pubmed",
                source_id="999001",
                title="Supplemental marker evidence",
                abstract=(
                    "Marker X was associated with outcome in the supplemental cohort."
                ),
                pmid="999001",
                publication_types=["Journal Article"],
            )
        ]

    async def fake_empty_search(
        _provider: object,
        _query: str,
        *,
        limit: int,
    ) -> list[LiteratureRecord]:
        assert limit == 10
        return []

    def fake_build_model(_client: httpx.AsyncClient, _settings: Settings) -> SequentialModel:
        return model

    async def override_session():  # type: ignore[no-untyped-def]
        async with sessions() as session:
            yield session

    monkeypatch.setattr(brainstorm_api, "async_session_factory", sessions)
    monkeypatch.setattr(brainstorm_api, "retrieve_with_cache", fake_retrieve_with_cache)
    monkeypatch.setattr(
        research_literature.PubMedProvider,
        "search",
        fake_pubmed_search,
    )
    monkeypatch.setattr(
        research_literature.EuropePmcProvider,
        "search",
        fake_empty_search,
    )
    monkeypatch.setattr(
        research_literature.OpenAlexProvider,
        "search",
        fake_empty_search,
    )
    monkeypatch.setattr(
        research_literature.CrossrefProvider,
        "search",
        fake_empty_search,
    )
    monkeypatch.setattr(brainstorm_api, "build_model_provider", fake_build_model)
    app.dependency_overrides[brainstorm_api.get_session] = override_session
    app.state.cache = object()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/brainstorm/sessions/{session_id}/messages",
                json={"content": "Explore marker X as an outcome biomarker"},
            )
    finally:
        app.dependency_overrides.clear()

    async with sessions() as session:
        literature = await session.scalar(
            select(BrainstormLiterature).where(
                BrainstormLiterature.session_id == session_id,
                BrainstormLiterature.paper_id == supplemental_paper_id,
            )
        )
        tag_rows = (
            await session.execute(
                select(Tag.name, PaperTag.origin)
                .join(PaperTag, PaperTag.tag_id == Tag.id)
                .where(
                    PaperTag.project_id == project_id,
                    PaperTag.paper_id == supplemental_paper_id,
                )
            )
        ).all()
        project_link = await session.get(
            ProjectPaper,
            (project_id, supplemental_paper_id),
        )
        stored_session = await session.get(BrainstormSession, session_id)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_count"] == 1
    assert payload["auto_ingested_paper_ids"] == [str(supplemental_paper_id)]
    first_global = observed_collection_ids.index(None)
    assert first_global > 0
    assert all(value == collection_id for value in observed_collection_ids[:first_global])
    assert all(value is None for value in observed_collection_ids[first_global:])
    assert len(observed_collection_ids) <= 12
    assert len(observed_pubmed_queries) == 3
    assert all(limit == 10 for _, limit in observed_pubmed_queries)
    assert observed_pubmed_queries[0][0] == "Explore marker X as an outcome biomarker"
    assert observed_pubmed_queries[1][0] == "marker X outcome"
    assert literature is not None
    assert literature.turn_number == 1
    assert literature.query == "Explore marker X as an outcome biomarker"
    assert literature.tags_applied == ["实验参考", f"会话{session_number}", "自动检索"]
    brainstorm_tags = {name for name, origin in tag_rows if origin == "brainstorm"}
    assert brainstorm_tags == {"实验参考", f"会话{session_number}", "自动检索"}
    assert project_link is not None
    assert stored_session is not None
    assert stored_session.status == "awaiting_confirmation"
    await engine.dispose()  # type: ignore[attr-defined]
