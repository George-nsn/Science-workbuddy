from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


def _clean_string_list(value: object, *, limit: int) -> list[str]:
    if value is None:
        return []
    values: list[object] = []
    if isinstance(value, str):
        values.extend(value.splitlines() if "\n" in value else [value])
    elif isinstance(value, list | tuple | set):
        values.extend(value)
    else:
        values.append(value)
    cleaned: list[str] = []
    for item in values:
        if isinstance(item, dict):
            continue
        text = str(item).strip().lstrip("-•").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:limit]


class ScientificSubQuestion(BaseModel):
    title: str = ""
    hypothesis: str = ""
    prediction: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, value: object) -> object:
        if isinstance(value, str):
            return {"title": value}
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized["title"] = (
            normalized.get("title")
            or normalized.get("question")
            or normalized.get("sub_question")
            or ""
        )
        h1 = str(normalized.get("h1") or "").strip()
        h0 = str(normalized.get("h0") or "").strip()
        normalized["hypothesis"] = normalized.get("hypothesis") or "；".join(
            part for part in (f"H1：{h1}" if h1 else "", f"H0：{h0}" if h0 else "") if part
        )
        normalized["prediction"] = (
            normalized.get("prediction")
            or normalized.get("quantitative_prediction")
            or normalized.get("predicted_outcome")
            or ""
        )
        return normalized

    @field_validator("title", "hypothesis", "prediction", mode="before")
    @classmethod
    def _clean_text(cls, value: object) -> str:
        return "" if value is None else str(value).strip()


class QuestionAgentOutput(BaseModel):
    overarching_question: str = Field(
        default="",
        description="拟解决的核心大科学问题；一个宏观、战略且可由子问题共同回答的问题",
    )
    scientific_question: str = Field(
        default="",
        description="兼容旧版客户端的核心科学问题字段",
    )
    hypothesis: str = Field(default="", description="兼容旧版客户端的总假说摘要")
    sub_questions: list[ScientificSubQuestion] = Field(
        default_factory=list,
        max_length=3,
        description="2-3 个可证伪子科学问题，每项含标题、H1/H0 假说与量化预测",
    )
    landscape_and_trends: str = Field(default="", description="国内外现状与前沿发展趋势综述")
    theoretical_significance: str = Field(default="", description="理论科学意义")
    application_value: str = Field(default="", description="应用价值与转化前景")
    background_summary: str = Field(default="", description="选题背景与领域前沿现状")
    significance: str = Field(default="", description="科学意义与应用价值")
    predictions: list[str] = Field(default_factory=list, max_length=8)
    innovation_rationale: list[str] = Field(default_factory=list, max_length=8)
    feasibility: list[str] = Field(default_factory=list, max_length=8)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=8)
    pubmed_queries: list[str] = Field(default_factory=list, max_length=2)
    questions_to_user: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("sub_questions", mode="before")
    @classmethod
    def _clean_sub_questions(cls, value: object) -> list[object]:
        if value is None:
            return []
        values = value if isinstance(value, list | tuple) else [value]
        return [item for item in values if item is not None][:3]

    @field_validator(
        "predictions",
        "innovation_rationale",
        "feasibility",
        "assumptions",
        "evidence_ids",
        "evidence_gaps",
        "pubmed_queries",
        "questions_to_user",
        mode="before",
    )
    @classmethod
    def _clean_lists(cls, value: object, info: object) -> list[str]:
        field_name = str(getattr(info, "field_name", ""))
        limits = {
            "predictions": 8,
            "innovation_rationale": 8,
            "feasibility": 8,
            "assumptions": 8,
            "evidence_gaps": 8,
            "pubmed_queries": 2,
            "questions_to_user": 6,
        }
        return _clean_string_list(value, limit=limits.get(field_name, 1000))

    @model_validator(mode="after")
    def _synchronize_legacy_fields(self) -> Self:
        if not self.overarching_question:
            self.overarching_question = self.scientific_question
        if not self.scientific_question:
            self.scientific_question = self.overarching_question
        if not self.landscape_and_trends:
            self.landscape_and_trends = self.background_summary
        if not self.background_summary:
            self.background_summary = self.landscape_and_trends
        if not self.theoretical_significance:
            self.theoretical_significance = self.significance
        if not self.significance:
            self.significance = "；".join(
                value
                for value in (self.theoretical_significance, self.application_value)
                if value
            )
        if not self.overarching_question:
            raise ValueError("An overarching or scientific question is required")
        return self


class ResearchBackgroundOutput(BaseModel):
    landscape_summary: str = Field(default="", description="领域现状与前沿进展综述")
    knowledge_gaps: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="核心争议与研究空白列表",
    )
    rationale_and_significance: str = Field(default="", description="选题立项依据与科学意义")
    potential_angles: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="探索性攻坚路线建议",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="引用的有效 Evidence ID 列表",
    )


class ExperimentWorkPackage(BaseModel):
    title: str
    purpose: str
    approach: str
    model_system: str | None = None
    controls: list[str] = Field(default_factory=list, max_length=8)
    endpoints: list[str] = Field(default_factory=list, max_length=8)
    quality_checks: list[str] = Field(default_factory=list, max_length=8)
    decision_point: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class MaterialCandidate(BaseModel):
    name: str
    category: str
    specification: str
    manufacturer: str | None = None
    catalog_model: str | None = None
    verification_status: Literal[
        "direct_evidence",
        "limited_evidence",
        "commercial_only",
        "candidate_recommended",
        "web_matched",
        "unverified_candidate",
    ] = "unverified_candidate"
    web_source_url: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    verification_note: str = ""

    @model_validator(mode="after")
    def prevent_unsupported_models(self) -> Self:
        allowed_statuses = ("candidate_recommended", "web_matched", "commercial_only")
        if (
            (self.manufacturer or self.catalog_model)
            and not self.evidence_ids
            and self.verification_status not in allowed_statuses
        ):
            raise ValueError(
                "Manufacturer/model candidates require supplied evidence IDs; "
                "otherwise leave them null or use candidate_recommended/web_matched status"
            )
        if self.catalog_model and self.verification_status == "unverified_candidate":
            raise ValueError("An exact model cannot be marked as an unverified candidate")
        return self


class ExperimentAgentOutput(BaseModel):
    design_summary: str
    objectives: list[str] = Field(default_factory=list, max_length=8)
    work_packages: list[ExperimentWorkPackage] = Field(default_factory=list, max_length=10)
    technical_route_mermaid: str | None = Field(
        default=None,
        description="兼容旧版输出；多层因果树 Mermaid 由 Coordinator 负责",
    )
    materials: list[MaterialCandidate] = Field(default_factory=list, max_length=40)
    analysis_plan: list[str] = Field(default_factory=list, max_length=10)
    reproducibility_plan: list[str] = Field(default_factory=list, max_length=10)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)
    safety_flags: list[str] = Field(default_factory=list, max_length=10)


class MethodModule(BaseModel):
    title: str
    objective: str
    principle: str
    inputs_and_material_categories: list[str] = Field(default_factory=list, max_length=15)
    prerequisites: list[str] = Field(default_factory=list, max_length=12)
    staged_procedure: list[str] = Field(default_factory=list, max_length=30)
    critical_variables: list[str] = Field(default_factory=list, max_length=15)
    controls: list[str] = Field(default_factory=list, max_length=12)
    replication_randomization_blinding: list[str] = Field(default_factory=list, max_length=12)
    quality_control: list[str] = Field(default_factory=list, max_length=15)
    acceptance_and_decision_criteria: list[str] = Field(default_factory=list, max_length=12)
    data_capture_and_analysis: list[str] = Field(default_factory=list, max_length=15)
    failure_modes_and_troubleshooting: list[str] = Field(default_factory=list, max_length=18)
    parameter_gaps: list[str] = Field(default_factory=list, max_length=15)
    evidence_ids: list[str] = Field(default_factory=list)


class MethodAgentOutput(BaseModel):
    method_overview: str
    modules: list[MethodModule] = Field(default_factory=list, max_length=12)
    cross_module_timeline: list[str] = Field(default_factory=list, max_length=15)
    reproducibility_checklist: list[str] = Field(default_factory=list, max_length=20)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=15)
    safety_flags: list[str] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(default_factory=list)


class CriticAgentOutput(BaseModel):
    strengths: list[str] = Field(default_factory=list, max_length=10)
    innovation_assessment: list[str] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=12)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=10)
    biases: list[str] = Field(default_factory=list, max_length=10)
    feasibility_risks: list[str] = Field(default_factory=list, max_length=10)
    fatal_flaws: list[str] = Field(default_factory=list, max_length=6)
    safety_flags: list[str] = Field(default_factory=list, max_length=10)
    questions_to_user: list[str] = Field(default_factory=list, max_length=6)
    evidence_ids: list[str] = Field(default_factory=list)


class RefinementAnalysisOutput(BaseModel):
    preserved_elements: list[str] = Field(default_factory=list, max_length=12)
    issues: list[str] = Field(default_factory=list, max_length=15)
    proposed_changes: list[str] = Field(default_factory=list, max_length=15)
    evidence_conflicts: list[str] = Field(default_factory=list, max_length=10)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)
    pubmed_queries: list[str] = Field(default_factory=list, max_length=2)
    questions_to_user: list[str] = Field(default_factory=list, max_length=8)


class ChangeLogItem(BaseModel):
    section: str
    change: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    user_confirmation_required: bool = True


class CoordinatorOutput(BaseModel):
    title: str
    proposal_background: str = Field(
        default="",
        description="第一章：选题背景与国内外进展",
    )
    scientific_question_and_hypothesis: str = Field(
        default="",
        description="第二章：大科学问题与子问题假说链",
    )
    scientific_significance: str = Field(
        default="",
        description="第三章：理论科学意义与应用价值",
    )
    technical_route_summary: str = Field(
        default="",
        description="第四章：总体实验架构、多臂对照体系与技术路线",
    )
    novelty_and_limitations: str = Field(
        default="",
        description="第五章：创新性、局限性审判与待确认决策",
    )
    response_markdown: str
    technical_route_mermaid: str | None = None
    confirmation_questions: list[str] = Field(default_factory=list, max_length=12)
    safety_flags: list[str] = Field(default_factory=list, max_length=30)
    evidence_ids: list[str] = Field(default_factory=list)
    revised_document_markdown: str | None = None
    change_log: list[ChangeLogItem] = Field(default_factory=list, max_length=30)

    @field_validator("confirmation_questions", mode="before")
    @classmethod
    def _truncate_confirmation_questions(cls, v: object) -> list[str]:
        return _clean_string_list(v, limit=4)


class PlanDirection(BaseModel):
    direction_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=2, max_length=240)
    rationale: str = Field(min_length=2, max_length=2000)
    why_hot: list[str] = Field(default_factory=list, max_length=6)
    novelty_points: list[str] = Field(default_factory=list, max_length=6)
    feasibility_notes: list[str] = Field(default_factory=list, max_length=6)
    key_risks: list[str] = Field(default_factory=list, max_length=6)
    evidence_ids: list[str] = Field(default_factory=list)
    web_sources: list[str] = Field(default_factory=list, max_length=10)


class PreferenceQuestion(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=2, max_length=500)
    kind: Literal["single_choice", "multi_choice", "scale", "text"]
    options: list[str] = Field(default_factory=list, max_length=12)
    required: bool = True
    rationale: str = Field(default="", max_length=500)


class PlanDiscoveryOutput(BaseModel):
    directions: list[PlanDirection] = Field(min_length=1, max_length=6)
    preference_questions: list[PreferenceQuestion] = Field(
        default_factory=list,
        max_length=10,
    )
    evidence_gaps: list[str] = Field(default_factory=list, max_length=10)
    safety_flags: list[str] = Field(default_factory=list, max_length=10)


class PreferenceProfile(BaseModel):
    objective_type: str | None = Field(default=None, max_length=200)
    model_system: str | None = Field(default=None, max_length=300)
    endpoint_priority: list[str] = Field(default_factory=list, max_length=10)
    budget_level: Literal["low", "medium", "high"] | None = None
    timeline_weeks: int | None = Field(default=None, ge=1, le=520)
    sample_availability: str | None = Field(default=None, max_length=1000)
    risk_tolerance: Literal["conservative", "balanced", "aggressive"] | None = None
    must_have_constraints: list[str] = Field(default_factory=list, max_length=15)
    free_text: str = Field(default="", max_length=5000)
