from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SynthesisOutlineSectionSpec(BaseModel):
    section_key: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=2, max_length=300)
    purpose: str = Field(default="", max_length=2000)
    level: int = Field(default=1, ge=1, le=4)
    parent_key: str | None = Field(default=None, max_length=96)
    outline: list[str] = Field(default_factory=list, max_length=12)
    retrieval_queries: list[str] = Field(default_factory=list, max_length=4)
    target_words: int = Field(default=1200, ge=300, le=8000)


class SourceRelation(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    relation: Literal[
        "supports",
        "complements",
        "contradicts",
        "method_precedes_result",
        "same_topic",
    ]
    description: str = Field(max_length=1000)


class FigurePlan(BaseModel):
    source_id: str = Field(min_length=1, max_length=64)
    table_index: int = Field(default=0, ge=0, le=100)
    title: str = Field(min_length=2, max_length=300)
    caption: str = Field(default="", max_length=1000)
    chart_type: Literal[
        "auto", "bar", "line", "scatter", "distribution", "heatmap"
    ] = "auto"
    section_keys: list[str] = Field(default_factory=list, max_length=10)


class DocumentUnderstandingOutput(BaseModel):
    working_title: str = Field(min_length=2, max_length=300)
    central_question: str = Field(min_length=2, max_length=2000)
    thesis_statement: str = Field(min_length=2, max_length=3000)
    global_summary: str = Field(min_length=2, max_length=6000)
    glossary: dict[str, str] = Field(default_factory=dict)
    source_relations: list[SourceRelation] = Field(default_factory=list, max_length=60)
    outline: list[SynthesisOutlineSectionSpec] = Field(min_length=3, max_length=20)
    literature_queries: list[str] = Field(default_factory=list, max_length=8)
    figure_plan: list[FigurePlan] = Field(default_factory=list, max_length=20)


class SectionDigest(BaseModel):
    summary: str = Field(min_length=2, max_length=3000)
    key_terms: list[str] = Field(default_factory=list, max_length=20)
    claims: list[str] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=40)
    source_ids: list[str] = Field(default_factory=list, max_length=40)


class SectionReview(BaseModel):
    verdict: Literal["approved", "revise"]
    logic_issues: list[str] = Field(default_factory=list, max_length=12)
    semantic_issues: list[str] = Field(default_factory=list, max_length=12)
    citation_issues: list[str] = Field(default_factory=list, max_length=12)
    missing_content: list[str] = Field(default_factory=list, max_length=12)
    revision_instructions: list[str] = Field(default_factory=list, max_length=12)
    concise_summary: str = Field(default="", max_length=2000)


class GlobalReview(BaseModel):
    verdict: Literal["approved", "revise"]
    overall_assessment: str = Field(max_length=3000)
    logic_issues: list[str] = Field(default_factory=list, max_length=20)
    terminology_issues: list[str] = Field(default_factory=list, max_length=20)
    citation_issues: list[str] = Field(default_factory=list, max_length=20)
    structural_issues: list[str] = Field(default_factory=list, max_length=20)
    affected_section_keys: list[str] = Field(default_factory=list, max_length=20)
    revision_instructions: list[str] = Field(default_factory=list, max_length=30)
