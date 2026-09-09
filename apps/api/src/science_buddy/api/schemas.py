from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from science_buddy.domain.providers import AuthorRecord, MeSHHeadingRecord
from science_buddy.services.literature.discovery import SourceReference
from science_buddy.services.research import ResearchResult

LiteratureProviderName = Literal["pubmed", "europe_pmc", "openalex", "crossref"]
MemoryEntityName = Literal[
    "project_fact",
    "research_step",
    "verified_claim",
    "failed_route",
]
MemoryConsumerName = Literal["brainstorm", "research_planner", "synthesis"]
MemoryFactStatus = Literal["active", "superseded", "retracted"]


def default_literature_providers() -> list[LiteratureProviderName]:
    return ["pubmed", "europe_pmc", "openalex", "crossref"]


def default_memory_entities() -> list[MemoryEntityName]:
    return ["project_fact", "research_step"]


def default_memory_statuses() -> list[MemoryFactStatus]:
    return ["active"]


class LiteratureSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    providers: list[LiteratureProviderName] = Field(
        default_factory=default_literature_providers,
        min_length=1,
        max_length=4,
    )
    limit: int = Field(default=20, ge=1, le=100)
    project_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class LiteratureSearchItem(BaseModel):
    key: str
    title: str
    abstract: str | None
    pmid: str | None
    pmcid: str | None
    doi: str | None
    journal: str | None
    publication_date: date | None
    publication_year: int | None
    authors: list[AuthorRecord]
    publication_types: list[str]
    mesh_headings: list[MeSHHeadingRecord]
    is_open_access: bool
    full_text_url: str | None
    sources: list[SourceReference]


class LiteratureSearchResponse(BaseModel):
    search_run_id: UUID
    query: str
    items: list[LiteratureSearchItem]


class LiteratureImportRequest(BaseModel):
    references: list[SourceReference] = Field(min_length=1, max_length=20)
    project_id: UUID | None = None


class ImportedPaper(BaseModel):
    paper_id: UUID
    created: bool
    chunks_created: int


class LiteratureImportResponse(BaseModel):
    project_id: UUID
    papers: list[ImportedPaper]


class PaperTagResponse(BaseModel):
    id: UUID
    name: str
    kind: str
    origin: Literal["auto", "manual", "brainstorm"]


class TagFilterResponse(BaseModel):
    id: UUID
    name: str
    kind: str
    paper_count: int


class JournalMetricResponse(BaseModel):
    source: Literal["openalex"]
    two_year_mean_citedness: float | None
    open_quartile: str | None
    percentile: float | None
    importance_score: float
    quartile_basis: dict[str, object]
    updated_date: str | None
    note: str


class LibraryPaper(BaseModel):
    id: UUID
    title: str
    abstract: str | None
    abstract_source: str | None
    pmid: str | None
    pmcid: str | None
    doi: str | None
    journal: str | None
    publication_year: int | None
    publication_types: list[str]
    is_open_access: bool
    open_access_status: str
    open_access_url: str | None
    is_retracted: bool
    retraction_status: str
    citation_count: int | None
    influential_citation_count: int | None
    journal_metric: JournalMetricResponse | None
    quality_signals: dict[str, object]
    metadata_sources: list[str]
    tags: list[PaperTagResponse]
    tags_manually_curated: bool


class LibraryResponse(BaseModel):
    project_id: UUID
    project_name: str
    papers: list[LibraryPaper]
    available_tags: list[TagFilterResponse]


class LiteratureArchiveCreateRequest(BaseModel):
    project_id: UUID
    paper_ids: list[UUID] = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_archive_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("archive name must not be blank")
        return cleaned


class LiteratureArchiveResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    paper_count: int
    origin: Literal["manual", "upload_batch"]
    created_at: str


class LiteratureArchivesResponse(BaseModel):
    project_id: UUID
    archives: list[LiteratureArchiveResponse]


class LiteratureMetadataEnrichRequest(BaseModel):
    project_id: UUID
    paper_ids: list[UUID] | None = Field(default=None, max_length=100)


class LiteratureMetadataEnrichItem(BaseModel):
    paper_id: UUID
    sources: list[str]
    references_linked: int
    errors: list[str]


class LiteratureMetadataEnrichResponse(BaseModel):
    project_id: UUID
    processed: int
    enriched: int
    failed: int
    references_linked: int
    items: list[LiteratureMetadataEnrichItem]


class BulkPaperDeleteRequest(BaseModel):
    project_id: UUID
    paper_ids: list[UUID] = Field(min_length=1, max_length=500)


class BulkPaperDeleteResponse(BaseModel):
    project_id: UUID
    requested: int
    removed_from_project: int
    deleted_globally: int
    collections_updated: int
    collections_deleted: int


class PaperTagsUpdateRequest(BaseModel):
    project_id: UUID
    labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, labels: list[str]) -> list[str]:
        cleaned = [" ".join(label.split()) for label in labels]
        if any(not label or len(label) > 80 for label in cleaned):
            raise ValueError("Tag names must contain 1-80 characters")
        return list(dict.fromkeys(cleaned))


class PaperTagsResponse(BaseModel):
    project_id: UUID
    paper_id: UUID
    manually_curated: bool
    tags: list[PaperTagResponse]


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    project_id: UUID
    workflow_id: UUID | None = None
    collection_id: UUID | None = None
    limit: int = Field(default=12, ge=1, le=30)
    strategy: Literal["auto", "direct", "decomposition", "deep_research"] = "auto"
    max_subqueries: int = Field(default=4, ge=1, le=6)
    max_followup_rounds: int = Field(default=1, ge=0, le=1)


class RetrievalItem(BaseModel):
    chunk_id: UUID
    evidence_id: str
    text: str
    score: float
    source_locator: dict[str, object]
    paper_id: UUID | None
    role: Literal["anchor", "neighbor"]
    anchor_chunk_id: UUID | None
    traces: list["RetrievalTraceItem"]


class RetrievalTraceItem(BaseModel):
    route: str
    rank: int
    raw_score: float
    weighted_rrf: float


class QueryExpansionItem(BaseModel):
    source: str
    target: str


class RetrievalQueryPlanResponse(BaseModel):
    language: Literal["zh", "en", "mixed"]
    original_query: str
    english_query: str | None
    expansions: list[QueryExpansionItem]


class ResearchSubqueryResponse(BaseModel):
    subquery_id: str
    focus: str
    query: str


class ResearchRoutingDecisionResponse(BaseModel):
    question_type: Literal[
        "identifier",
        "comparison",
        "mechanism",
        "pico",
        "methodology",
        "systematic_review",
        "latest_progress",
        "general",
    ]
    strategy: Literal["direct", "decomposition", "deep_research"]
    confidence: float
    reason_codes: list[str]
    max_followup_rounds: int
    version: str
    subqueries: list[ResearchSubqueryResponse]


class RetrievalStepAuditResponse(BaseModel):
    subquery_id: str
    focus: str
    query: str
    round: int
    candidates: int
    cache_level: str
    route_errors: list[str]


class RetrievalRouteSummary(BaseModel):
    route: str
    candidates: int
    elapsed_ms: float
    error: str | None
    metrics: dict[str, str | int | float] = Field(default_factory=dict)


class RetrievalRerankerSummary(BaseModel):
    enabled: bool
    applied: bool
    model: str | None
    candidates: int
    elapsed_ms: float
    error: str | None


class RetrievalResponse(BaseModel):
    workflow_id: UUID
    retrieval_mode: Literal["identifier", "hybrid-sparse", "hybrid-dense"]
    retrieval_version: str
    query_plan: RetrievalQueryPlanResponse
    routes: list[RetrievalRouteSummary]
    reranker: RetrievalRerankerSummary
    excluded_retracted: int
    elapsed_ms: float
    cache_level: Literal["miss", "l1-memory", "l2-redis", "l3-sqlite"]
    routing: ResearchRoutingDecisionResponse
    retrieval_steps: list[RetrievalStepAuditResponse]
    items: list[RetrievalItem]


class FullTextImportRequest(BaseModel):
    project_id: UUID
    paper_id: UUID


class DocumentImportResponse(BaseModel):
    project_id: UUID
    paper_id: UUID
    asset_id: UUID
    evidence_depth: str
    chunks_created: int
    duplicate: bool


class DocumentBatchItemResponse(BaseModel):
    filename: str
    relative_path: str
    status: Literal["imported", "failed"]
    result: DocumentImportResponse | None = None
    error: str | None = None


class DocumentBatchImportResponse(BaseModel):
    total: int
    imported: int
    failed: int
    duplicates: int
    paper_ids: list[UUID]
    archive: LiteratureArchiveResponse | None
    items: list[DocumentBatchItemResponse]


class PdfEnrichmentRequest(BaseModel):
    project_id: UUID


class PdfEnrichmentResponse(BaseModel):
    project_id: UUID
    processed: int
    summaries_updated: int
    tagged: int
    failed: int


class WorkbenchNoteUpsertRequest(BaseModel):
    project_id: UUID
    title: str = Field(min_length=1, max_length=240)
    content_markdown: str = Field(default="", max_length=1_000_000)

    @field_validator("title")
    @classmethod
    def clean_note_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("note title must not be blank")
        return cleaned


class WorkbenchNoteResponse(BaseModel):
    id: UUID
    project_id: UUID
    entry_date: date
    title: str
    content_markdown: str
    created_at: datetime
    updated_at: datetime


class RetentionPolicyRequest(BaseModel):
    trash_retention_days: int = Field(ge=1, le=3650)


class RetentionPolicyResponse(BaseModel):
    project_id: UUID
    trash_retention_days: int


class TrashItemResponse(BaseModel):
    entity_type: Literal[
        "brainstorm_session",
        "research_run",
        "workbench_note",
        "workbench_task",
        "workbench_attachment",
    ]
    entity_id: UUID
    title: str
    deleted_at: datetime
    purge_after: datetime


class RecycleBinResponse(BaseModel):
    project_id: UUID
    retention_days: int
    items: list[TrashItemResponse]


class WorkbenchTaskCreateRequest(BaseModel):
    project_id: UUID
    title: str = Field(min_length=1, max_length=300)
    work_date: date | None = None
    priority: Literal["low", "medium", "high"] = "medium"

    @field_validator("title")
    @classmethod
    def clean_task_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("task title must not be blank")
        return cleaned


class WorkbenchTaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    work_date: date | None = None
    status: Literal["todo", "in_progress", "done"] | None = None
    priority: Literal["low", "medium", "high"] | None = None

    @field_validator("title")
    @classmethod
    def clean_updated_task_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("task title must not be blank")
        return cleaned

    @model_validator(mode="after")
    def require_task_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one task field must be provided")
        return self


class WorkbenchTaskResponse(BaseModel):
    id: UUID
    project_id: UUID
    work_date: date | None
    title: str
    status: Literal["todo", "in_progress", "done"]
    priority: Literal["low", "medium", "high"]
    completed_at: datetime | None
    source_kind: str | None
    source_id: UUID | None
    source_section: str | None
    created_at: datetime
    updated_at: datetime


class WorkbenchAttachmentResponse(BaseModel):
    id: UUID
    project_id: UUID
    note_id: UUID
    filename: str
    media_type: str
    byte_size: int
    width: int
    height: int
    ocr_status: Literal["complete", "empty", "failed", "skipped"]
    ocr_text: str
    ocr_error: str | None
    attachment_kind: Literal["uploaded_image", "generated_plot"]
    generator: str | None
    source_table_hash: str | None
    source_table_markdown: str | None
    render_spec: dict[str, object]
    render_revision: int
    caption: str | None
    content_url: str
    created_at: datetime


class WorkbenchPlotRequest(BaseModel):
    project_id: UUID
    markdown_table: str = Field(min_length=10, max_length=200_000)
    title: str | None = Field(default=None, max_length=240)
    caption: str | None = Field(default=None, max_length=1000)
    intent: str = Field(default="", max_length=2000)
    allow_model_planning: bool = False
    chart_type: Literal[
        "auto",
        "bar",
        "line",
        "scatter",
        "distribution",
        "heatmap",
    ] = "auto"
    x_column: str | None = Field(default=None, max_length=200)
    y_columns: list[str] = Field(default_factory=list, max_length=12)
    group_column: str | None = Field(default=None, max_length=200)
    error_column: str | None = Field(default=None, max_length=200)
    font_size: int = Field(default=10, ge=7, le=28)
    palette: Literal["journal", "npg", "nejm", "lancet", "jama", "colorblind"] = (
        "journal"
    )
    line_width: float = Field(default=1.8, ge=0.5, le=6)
    point_size: float = Field(default=32, ge=8, le=160)
    figure_width: float = Field(default=7.2, ge=4, le=16)
    figure_height: float = Field(default=4.6, ge=3, le=12)
    dpi: int = Field(default=300, ge=120, le=600)
    show_grid: bool = False
    legend_position: Literal["best", "top", "bottom", "left", "right", "none"] = (
        "best"
    )
    data_layout: Literal["auto", "long", "wide", "summary"] = "auto"
    condition_column: str | None = Field(default=None, max_length=200)
    value_column: str | None = Field(default=None, max_length=200)
    replicate_column: str | None = Field(default=None, max_length=200)
    replicate_columns: list[str] = Field(default_factory=list, max_length=12)
    summary_stat: Literal["mean_sd", "mean_sem", "mean_ci95"] = "mean_sd"
    show_all_points: bool = True
    show_sample_size: bool = True
    replicate_unit: Literal["biological", "technical"] = "biological"
    pairing_mode: Literal["independent", "paired"] = "independent"


class WorkbenchPlotRerenderRequest(BaseModel):
    markdown_table: str | None = Field(default=None, min_length=10, max_length=200_000)
    title: str | None = Field(default=None, max_length=240)
    caption: str | None = Field(default=None, max_length=1000)
    intent: str = Field(default="", max_length=2000)
    allow_model_planning: bool = False
    chart_type: Literal[
        "auto", "bar", "line", "scatter", "distribution", "heatmap"
    ] | None = None
    x_column: str | None = Field(default=None, max_length=200)
    y_columns: list[str] | None = Field(default=None, max_length=12)
    group_column: str | None = Field(default=None, max_length=200)
    error_column: str | None = Field(default=None, max_length=200)
    font_size: int | None = Field(default=None, ge=7, le=28)
    palette: Literal["journal", "npg", "nejm", "lancet", "jama", "colorblind"] | None = None
    line_width: float | None = Field(default=None, ge=0.5, le=6)
    point_size: float | None = Field(default=None, ge=8, le=160)
    figure_width: float | None = Field(default=None, ge=4, le=16)
    figure_height: float | None = Field(default=None, ge=3, le=12)
    dpi: int | None = Field(default=None, ge=120, le=600)
    show_grid: bool | None = None
    legend_position: Literal["best", "top", "bottom", "left", "right", "none"] | None = None
    data_layout: Literal["auto", "long", "wide", "summary"] | None = None
    condition_column: str | None = Field(default=None, max_length=200)
    value_column: str | None = Field(default=None, max_length=200)
    replicate_column: str | None = Field(default=None, max_length=200)
    replicate_columns: list[str] | None = Field(default=None, max_length=12)
    summary_stat: Literal["mean_sd", "mean_sem", "mean_ci95"] | None = None
    show_all_points: bool | None = None
    show_sample_size: bool | None = None
    replicate_unit: Literal["biological", "technical"] | None = None
    pairing_mode: Literal["independent", "paired"] | None = None


class WorkbenchPlotResponse(BaseModel):
    attachment: WorkbenchAttachmentResponse
    chart_type: Literal["bar", "line", "scatter", "distribution", "heatmap"]
    rationale: str
    detected_columns: dict[str, Literal["numeric", "categorical", "temporal"]]
    warnings: list[str]
    markdown_image: str
    planning_source: Literal["deterministic", "model"]
    model_rationale: str | None = None


class WorkbenchDaySummary(BaseModel):
    entry_date: date
    has_note: bool
    note_title: str | None
    tasks_total: int
    tasks_done: int


class WorkbenchOverviewResponse(BaseModel):
    project_id: UUID
    project_name: str
    month: str
    days: list[WorkbenchDaySummary]
    tasks: list[WorkbenchTaskResponse]
    recent_notes: list[WorkbenchNoteResponse]


class WorkbenchDayResponse(BaseModel):
    project_id: UUID
    entry_date: date
    note: WorkbenchNoteResponse | None
    tasks: list[WorkbenchTaskResponse]
    attachments: list[WorkbenchAttachmentResponse]


class IndexProjectRequest(BaseModel):
    project_id: UUID


class IndexProjectResponse(BaseModel):
    project_id: UUID
    job_id: str
    status: Literal["queued"] = "queued"


class RagCollectionCreateRequest(BaseModel):
    project_id: UUID
    paper_ids: list[UUID] = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    build_vector: bool = True
    build_graph: bool = True

    @model_validator(mode="after")
    def require_rag_artifact(self) -> Self:
        if not self.build_vector and not self.build_graph:
            raise ValueError("Select vector indexing, knowledge graph, or both")
        return self


class RagCollectionResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    paper_count: int
    vector_status: str
    graph_status: str
    vector_job_id: str | None
    warning: str | None = None


class RagCollectionsResponse(BaseModel):
    project_id: UUID
    collections: list[RagCollectionResponse]


class ResearchAnswerRequest(BaseModel):
    project_id: UUID
    question: str = Field(min_length=2, max_length=2000)
    language: Literal["zh-CN", "en"] = "zh-CN"
    collection_id: UUID | None = None
    retrieval_limit: int = Field(default=12, ge=3, le=20)
    strategy: Literal["auto", "direct", "decomposition", "deep_research"] = "auto"
    max_subqueries: int = Field(default=4, ge=1, le=6)
    max_followup_rounds: int = Field(default=1, ge=0, le=2)
    allow_pubmed_search: bool = False
    allow_web_search: bool = False
    allow_auto_import: bool = False
    dynamic_planning: bool = True
    max_external_requests: int = Field(default=2, ge=0, le=8)
    model_depth: Literal["quick", "balanced", "deep", "max"] = "balanced"
    max_context_tokens: Literal[
        32768, 65536, 131072, 262144, 524288, 1000000
    ] = 65536


class ResearchToolActionResponse(BaseModel):
    tool: Literal[
        "local_retrieval",
        "graph_local_search",
        "graph_global_search",
        "graph_drift_search",
        "graph_path_search",
        "citation_graph",
        "scholarly_discovery",
        "controlled_web_search",
    ]
    reason: str
    queries: list[str]
    executed: bool
    results: int
    error: str | None = None


class ResearchClaimTargetResponse(BaseModel):
    claim_id: str
    statement: str
    claim_type: str
    priority: float
    falsifiable_prediction: str
    required_evidence_types: list[str]
    status: str
    evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    unresolved_reason: str | None


class ResearchActionResponse(BaseModel):
    round_number: int
    action_id: str
    action_type: str
    query: str
    target_claim_ids: list[str]
    rationale: str
    expected_information_gain: float
    novelty: float
    falsifiability: float
    estimated_cost: float
    requested_source: str
    requires_external_access: bool
    decision_score: float
    approved: bool
    rejection_reason: str | None


class ResearchPlanResponse(BaseModel):
    question_type: str
    strategy: str
    core_claims: list[ResearchClaimTargetResponse]
    required_subqueries: list[str]
    exploratory_subqueries: list[str]
    counterevidence_subqueries: list[str]
    allowed_sources: list[str]
    allowed_actions: list[str]
    max_rounds: int
    max_total_subqueries: int
    max_actions_per_round: int
    max_external_requests: int
    source: str
    planner_error: str | None


class SufficiencyVerdictResponse(BaseModel):
    round_number: int
    answer_coverage: float
    evidence_quality: float
    counterevidence_coverage: float
    alternative_hypothesis_coverage: float
    population_coverage: float
    methodological_coverage: float
    novelty_coverage: float
    unresolved_claim_ids: list[str]
    conflicting_claim_ids: list[str]
    proposed_followups: list[str]
    sufficient: bool
    stop_reason: str


class ResearchProgressStepResponse(BaseModel):
    step_number: int
    round_number: int
    step_type: str
    decision: str
    status: str


class ResearchAnswerResponse(BaseModel):
    run_id: UUID
    retrieval_mode: Literal["identifier", "hybrid-sparse", "hybrid-dense"]
    retrieval_version: str
    routing: ResearchRoutingDecisionResponse | None = None
    retrieval_steps: list[RetrievalStepAuditResponse] = Field(default_factory=list)
    tool_actions: list[ResearchToolActionResponse] = Field(default_factory=list)
    research_plan: ResearchPlanResponse | None = None
    research_actions: list[ResearchActionResponse] = Field(default_factory=list)
    sufficiency: list[SufficiencyVerdictResponse] = Field(default_factory=list)
    progress: list[ResearchProgressStepResponse] = Field(default_factory=list)
    result: ResearchResult


class MemoryRecallRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    entity_types: list[MemoryEntityName] = Field(
        default_factory=default_memory_entities,
        min_length=1,
        max_length=4,
    )
    categories: list[str] = Field(default_factory=list, max_length=20)
    statuses: list[MemoryFactStatus] = Field(
        default_factory=default_memory_statuses,
        max_length=3,
    )
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    created_after: datetime | None = None
    limit: int = Field(default=12, ge=1, le=50)


class MemoryRecallItemResponse(BaseModel):
    entity_type: Literal["project_fact", "research_step"]
    entity_id: UUID
    text: str
    category: str | None
    status: str
    confidence: float
    importance: float
    created_at: datetime
    dense_score: float
    lexical_score: float
    recent_score: float
    importance_score: float
    fused_score: float
    routes: list[str]
    source: dict[str, object]
    related: list[dict[str, object]]


class MemoryRecallResponse(BaseModel):
    project_id: UUID
    strategy: str
    indexed: int
    embedding_model: str
    items: list[MemoryRecallItemResponse]


class MemoryContextRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    consumer: MemoryConsumerName
    memory_types: list[MemoryEntityName] = Field(default_factory=list, max_length=4)
    categories: list[str] = Field(default_factory=list, max_length=20)
    statuses: list[MemoryFactStatus] = Field(
        default_factory=default_memory_statuses,
        max_length=3,
    )
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    created_after: datetime | None = None
    limit: int = Field(default=16, ge=1, le=50)


class MemoryContextItemResponse(BaseModel):
    memory_type: MemoryEntityName
    entity_id: UUID
    role: Literal[
        "project_context",
        "confirmed_project_fact",
        "planning_experience",
        "verified_claim_prior",
        "route_failure_experience",
    ]
    text: str
    category: str | None
    status: str
    confidence: float
    importance: float
    fused_score: float
    routes: list[str]
    source: dict[str, object]
    related: list[dict[str, object]]
    created_at: datetime
    evidence_eligible: Literal[False] = False


class MemoryContextResponse(BaseModel):
    project_id: UUID
    consumer: MemoryConsumerName
    allowed_memory_types: list[MemoryEntityName]
    strategy: str
    indexed: int
    embedding_model: str
    evidence_boundary: str
    items: list[MemoryContextItemResponse]


class ResearchPlanVersionResponse(BaseModel):
    version_number: int
    source: str
    parent_version_number: int | None
    added_claim_ids: list[str]
    removed_claim_ids: list[str]
    added_action_ids: list[str]
    removed_action_ids: list[str]
    added_queries: list[str]


class ResearchRoundTraceResponse(BaseModel):
    round_number: int
    phase: str
    status: str
    approved_actions: int
    rejected_actions: int
    external_actions: int
    action_budget_used: int
    action_budget_limit: int
    external_budget_used: int
    external_budget_limit: int
    new_evidence_count: int
    sufficient: bool | None
    stop_reason: str | None


class ClaimEvidenceMatrixRowResponse(BaseModel):
    claim_key: str
    statement: str
    status: str
    verification_label: str | None
    verification_confidence: float | None
    evidence_ids: list[str]
    contradicting_evidence_ids: list[str]


class ResearchTraceResponse(BaseModel):
    run_id: UUID
    plan_versions: list[ResearchPlanVersionResponse]
    rounds: list[ResearchRoundTraceResponse]
    claim_evidence_matrix: list[ClaimEvidenceMatrixRowResponse]
    progress: list[ResearchProgressStepResponse]


class ResearchReviewResponse(BaseModel):
    run_id: UUID
    markdown: str
    verified_claim_count: int
    citation_sentence_count: int


class ResearchFactsPublishRequest(BaseModel):
    confirmed: Literal[True]
    published_by: str = Field(default="user", min_length=1, max_length=64)


class ResearchFactsPublishResponse(BaseModel):
    run_id: UUID
    created: int
    reused: int
    conflicts: int
    fact_ids: list[UUID]
    published_at: datetime


class ToolRegistrationResponse(BaseModel):
    tool_id: str
    label: str
    provider: str
    input_schema: dict[str, object]
    permission: str
    cost_class: Literal["local", "free_external", "metered_external"]
    timeout_seconds: int
    evidence_eligible: bool
    mechanical_verification: str
    enabled: bool
    implementation_status: Literal["available", "registered_only"]


class ToolRegistryResponse(BaseModel):
    tools: list[ToolRegistrationResponse]


class ModelStatusResponse(BaseModel):
    configured: bool
    provider: str | None
    model: str | None


class ModelConfigurationRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("model")
    @classmethod
    def clean_model_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("model must not be blank")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def clean_base_url(cls, value: str | None) -> str | None:
        cleaned = value.strip().rstrip("/") if value else None
        return cleaned or None


class ModelConfigurationResponse(BaseModel):
    configured: bool
    provider: str | None
    model: str | None
    base_url: str | None
    has_api_key: bool
    source: Literal["environment", "local", "none"]


class ModelConnectionTestResponse(BaseModel):
    ok: Literal[True] = True
    provider: str
    model: str
    detail: str


class WebSearchConfigurationRequest(BaseModel):
    provider: Literal["tavily"] = "tavily"
    base_url: str = Field(default="https://api.tavily.com", max_length=2048)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    max_results: int = Field(default=5, ge=1, le=10)
    search_depth: Literal["basic", "advanced"] = "advanced"


class WebSearchConfigurationResponse(BaseModel):
    configured: bool
    provider: Literal["tavily"] | None
    base_url: str | None
    has_api_key: bool
    max_results: int
    search_depth: Literal["basic", "advanced"]
    source: Literal["environment", "local", "none"]


class WebSearchConnectionTestResponse(BaseModel):
    ok: Literal[True] = True
    provider: Literal["tavily"] = "tavily"
    detail: str


class ControlledWebSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    max_results: int | None = Field(default=None, ge=1, le=10)


class ControlledWebSearchItem(BaseModel):
    title: str
    url: str
    snippet: str
    score: float | None
    published_date: str | None


class ControlledWebSearchResponse(BaseModel):
    provider: Literal["tavily"] = "tavily"
    query: str
    items: list[ControlledWebSearchItem]


class ModelCatalogOptionResponse(BaseModel):
    id: str
    label: str
    category: str


class ModelCatalogProviderResponse(BaseModel):
    id: str
    label: str
    protocol: Literal["openai_compatible", "anthropic", "github_copilot"]
    description: str
    default_base_url: str
    api_key_required: bool
    models: list[ModelCatalogOptionResponse]


class ModelCatalogResponse(BaseModel):
    providers: list[ModelCatalogProviderResponse]


class GraphBuildRequest(BaseModel):
    project_id: UUID
    collection_id: UUID | None = None


class GraphBuildResponse(BaseModel):
    project_id: UUID
    nodes: int
    edges: int
    revision: int
    scope_key: str
    communities: int = 0
    reports: int = 0
    mentions: int = 0


class GraphNodeResponse(BaseModel):
    node_key: str
    node_type: str
    label: str
    properties: dict[str, object]


class GraphEdgeResponse(BaseModel):
    source_key: str
    relation: str
    target_key: str
    provenance: str
    confidence: float


class GraphCommunityResponse(BaseModel):
    id: UUID
    community_key: str
    level: int
    parent_community_id: UUID | None
    title: str
    member_count: int
    algorithm: str
    stats: dict[str, object]


class GraphCommunityReportResponse(BaseModel):
    id: UUID
    community_id: UUID
    title: str
    summary: str
    report_text: str
    evidence_bundle: dict[str, object]
    generated_by: str


class GraphSnapshotResponse(BaseModel):
    project_id: UUID
    scope_key: str
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    communities: list[GraphCommunityResponse] = Field(default_factory=list)
    reports: list[GraphCommunityReportResponse] = Field(default_factory=list)


class GraphRagQueryRequest(BaseModel):
    project_id: UUID
    query: str = Field(min_length=2, max_length=1000)
    collection_id: UUID | None = None
    mode: Literal["local", "global", "drift", "path"] = "local"
    seed_paper_ids: list[UUID] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=30)
    hops: int = Field(default=2, ge=1, le=4)
    persist_path_audit: bool = True


class GraphRagHitResponse(BaseModel):
    chunk_id: UUID
    paper_id: UUID
    score: float
    provenance: dict[str, object]


class GraphRagQueryResponse(BaseModel):
    project_id: UUID
    scope_key: str
    mode: Literal["local", "global", "drift", "path"]
    hits: list[GraphRagHitResponse]
    metrics: dict[str, str | int | float]
    paths: list[dict[str, object]]


class BrainstormSessionCreateRequest(BaseModel):
    project_id: UUID
    mode: Literal["exploration", "refinement"]
    title: str | None = Field(default=None, max_length=200)
    collection_id: UUID | None = None
    allow_pubmed_search: bool = True
    model_processing_allowed: bool = False
    workflow: Literal["classic", "plan"] = "classic"
    allow_web_search: bool = False
    model_depth: Literal["quick", "balanced", "deep", "max"] = "max"
    max_context_tokens: Literal[
        32768, 65536, 131072, 262144, 524288, 1000000
    ] = 1000000
    agent_background: str = Field(default="", max_length=6000)


class BrainstormSessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned


class BrainstormMessageRequest(BaseModel):
    content: str = Field(min_length=2, max_length=12000)


class BrainstormMessageResponse(BaseModel):
    id: UUID
    sequence_number: int
    role: str
    agent_name: str | None
    content: str
    payload: dict[str, object]
    evidence_ids: list[str]
    created_at: str


class BrainstormVersionResponse(BaseModel):
    id: UUID
    version_number: int
    kind: str
    parent_version_id: UUID | None
    content: str
    source_filename: str | None
    change_summary: list[dict[str, object]]
    created_at: str


class BrainstormSessionSummary(BaseModel):
    id: UUID
    project_id: UUID
    collection_id: UUID | None
    title: str
    mode: Literal["exploration", "refinement"]
    status: str
    session_number: int
    confirmation_round: int
    allow_pubmed_search: bool
    model_processing_allowed: bool
    workflow: Literal["classic", "plan"]
    phase: str
    allow_web_search: bool
    plan_snapshot: dict[str, object]
    model_depth: Literal["quick", "balanced", "deep", "max"]
    max_context_tokens: int
    agent_background: str
    created_at: str


class UsageSummaryResponse(BaseModel):
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    request_count: int
    retrieval_count: int
    cache_hits: int
    cache_hit_rate: float
    known_cost_usd: float
    cost_coverage_rate: float
    estimated_token_events: int


class BrainstormTurnUsageResponse(UsageSummaryResponse):
    turn_number: int
    message_id: UUID | None


class BrainstormSessionUsageResponse(BaseModel):
    session_id: UUID
    summary: UsageSummaryResponse
    turns: list[BrainstormTurnUsageResponse]


class BrainstormSessionDetail(BaseModel):
    session: BrainstormSessionSummary
    messages: list[BrainstormMessageResponse]
    versions: list[BrainstormVersionResponse]
    memory: "BrainstormSessionMemoryResponse | None" = None
    facts: list["ProjectFactResponse"] = Field(default_factory=list)
    usage: BrainstormSessionUsageResponse


class BrainstormSessionsResponse(BaseModel):
    project_id: UUID
    sessions: list[BrainstormSessionSummary]


class BrainstormTurnResponse(BaseModel):
    session_id: UUID
    status: str
    version_id: UUID
    version_number: int
    response_markdown: str
    technical_route_mermaid: str | None
    confirmation_questions: list[str]
    safety_flags: list[str]
    evidence_ids: list[str]
    evidence_count: int
    auto_ingested_paper_ids: list[UUID]
    message_id: UUID
    usage: UsageSummaryResponse


class BrainstormRestoreResponse(BaseModel):
    session_id: UUID
    version_id: UUID
    version_number: int
    restored_from_version_id: UUID
    restored_from_version_number: int


class BrainstormRegenerateRequest(BaseModel):
    message_id: UUID


class DashboardSeriesPointResponse(UsageSummaryResponse):
    bucket: str


class DashboardModelUsageResponse(UsageSummaryResponse):
    provider: str
    model: str


class DashboardResponse(BaseModel):
    project_id: UUID
    period: Literal["7d", "30d", "90d", "all"]
    granularity: Literal["day", "week", "month"]
    summary: UsageSummaryResponse
    series: list[DashboardSeriesPointResponse]
    models: list[DashboardModelUsageResponse]


class BrainstormPlanDiscoveryRequest(BaseModel):
    seed_interest: str = Field(min_length=2, max_length=2000)
    max_directions: int = Field(default=4, ge=3, le=6)


class PlanDirectionResponse(BaseModel):
    direction_id: str
    title: str
    rationale: str
    why_hot: list[str]
    novelty_points: list[str]
    feasibility_notes: list[str]
    key_risks: list[str]
    evidence_ids: list[str]
    web_sources: list[str]


class PreferenceQuestionResponse(BaseModel):
    question_id: str
    question: str
    kind: Literal["single_choice", "multi_choice", "scale", "text"]
    options: list[str]
    required: bool
    rationale: str


class BrainstormPlanSnapshotResponse(BaseModel):
    session_id: UUID
    phase: str
    directions: list[PlanDirectionResponse]
    preference_questions: list[PreferenceQuestionResponse]
    selected_direction_id: str | None
    preference_profile: dict[str, object]
    preference_answers: dict[str, object]
    readiness_score: float
    missing_fields: list[str]
    web_search_used: bool
    prefetch_job: dict[str, object] | None = None

    
class BrainstormBackgroundJobResponse(BaseModel):
    id: UUID
    session_id: UUID
    kind: Literal["plan_discovery", "plan_generation"]
    status: Literal["queued", "running", "retrying", "succeeded", "failed", "cancelled"]
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class BrainstormPreferenceRequest(BaseModel):
    selected_direction_id: str = Field(min_length=1, max_length=64)
    objective_type: str | None = Field(default=None, max_length=200)
    model_system: str | None = Field(default=None, max_length=300)
    endpoint_priority: list[str] = Field(default_factory=list, max_length=10)
    budget_level: Literal["low", "medium", "high"] | None = None
    timeline_weeks: int | None = Field(default=None, ge=1, le=520)
    sample_availability: str | None = Field(default=None, max_length=1000)
    risk_tolerance: Literal["conservative", "balanced", "aggressive"] | None = None
    must_have_constraints: list[str] = Field(default_factory=list, max_length=15)
    free_text: str = Field(default="", max_length=5000)
    answers: dict[str, object] = Field(default_factory=dict)


class BrainstormPrefetchRequest(BaseModel):
    selected_direction_id: str = Field(min_length=1, max_length=64)


class BrainstormPrefetchResponse(BaseModel):
    session_id: UUID
    direction_id: str
    status: Literal["running", "succeeded", "failed"]
    prefetched_paper_ids: list[str] = Field(default_factory=list)
    error: str | None = None



class BrainstormSourceUploadResponse(BaseModel):
    session_id: UUID
    version_id: UUID
    version_number: int
    filename: str
    characters: int


class BrainstormConfirmResponse(BaseModel):
    session_id: UUID
    status: Literal["finalized"]
    version_id: UUID
    version_number: int


class SynthesisSessionCreateRequest(BaseModel):
    project_id: UUID
    topic: str = Field(min_length=2, max_length=5000)
    title: str | None = Field(default=None, max_length=300)
    model_depth: Literal["quick", "balanced", "deep", "max"] = "deep"
    max_context_tokens: Literal[32768, 65536, 131072, 262144, 524288, 1000000] = 131072
    max_review_rounds: int = Field(default=1, ge=0, le=2)
    include_workbench_notes: bool = True
    allow_online_literature: bool = False


class SynthesisTextSourceRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=2, max_length=500_000)


class SynthesisSectionUpdateRequest(BaseModel):
    draft_markdown: str = Field(min_length=1, max_length=500_000)


class SynthesisSourceResponse(BaseModel):
    id: UUID
    source_type: Literal["workbench_note", "uploaded_file", "user_text"]
    source_ref_id: UUID | None
    filename: str
    media_type: str
    content_hash: str
    module_count: int
    summary: str
    relations: list[dict[str, object]]
    status: str
    error_message: str | None
    created_at: datetime


class SynthesisSectionResponse(BaseModel):
    id: UUID
    parent_id: UUID | None
    section_key: str
    ordinal: int
    level: int
    title: str
    purpose: str
    outline: list[str]
    draft_markdown: str
    section_summary: str
    source_ids: list[str]
    evidence_ids: list[str]
    status: str
    revision: int
    updated_at: datetime


class SynthesisReviewResponse(BaseModel):
    id: UUID
    section_id: UUID | None
    round_number: int
    scope: str
    verdict: str
    feedback: dict[str, object]
    affected_section_keys: list[str]
    created_at: datetime


class SynthesisAgentRunResponse(BaseModel):
    id: UUID
    section_id: UUID | None
    round_number: int
    agent_name: str
    output: dict[str, object]
    status: str
    error: str | None
    created_at: datetime


class SynthesisSessionSummaryResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    topic: str
    status: Literal["draft", "analyzing", "writing", "reviewing", "completed", "failed"]
    model_depth: Literal["quick", "balanced", "deep", "max"]
    max_context_tokens: int
    max_review_rounds: int
    include_workbench_notes: bool
    allow_online_literature: bool
    current_round: int
    section_count: int = 0
    source_count: int = 0
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class SynthesisSessionDetailResponse(SynthesisSessionSummaryResponse):
    template_profile: dict[str, object]
    document_map: dict[str, object]
    global_outline: list[dict[str, object]]
    global_summary: str
    glossary: dict[str, object]
    citation_ledger: dict[str, object]
    figure_manifest: list[dict[str, object]]
    manuscript_markdown: str
    sources: list[SynthesisSourceResponse]
    sections: list[SynthesisSectionResponse]
    reviews: list[SynthesisReviewResponse]
    agent_runs: list[SynthesisAgentRunResponse]


class SynthesisSessionsResponse(BaseModel):
    project_id: UUID
    sessions: list[SynthesisSessionSummaryResponse]


class SynthesisJobResponse(BaseModel):
    id: UUID
    session_id: UUID
    status: Literal["queued", "running", "retrying", "succeeded", "failed", "cancelled"]
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class BrainstormTaskDispatchRequest(BaseModel):
    work_date: date | None = None
    priority: Literal["low", "medium", "high"] = "medium"


class BrainstormTaskDispatchResponse(BaseModel):
    session_id: UUID
    message_id: UUID
    sections_detected: int
    created_count: int
    skipped_existing: int
    tasks: list[WorkbenchTaskResponse]


class BrainstormSessionMemoryResponse(BaseModel):
    session_id: UUID
    source_message_id: UUID
    turn_number: int
    summary_markdown: str
    summary_data: dict[str, object]
    updated_at: datetime


class ProjectFactResponse(BaseModel):
    id: UUID
    project_id: UUID
    category: str
    statement: str
    source_type: str
    source_id: UUID
    source_session_id: UUID | None
    source_locator: dict[str, object]
    confidence: float
    importance: float
    status: Literal["active", "superseded", "retracted"]
    created_at: datetime
    updated_at: datetime


class ProjectFactsResponse(BaseModel):
    project_id: UUID
    facts: list[ProjectFactResponse]


class ProjectFactStatusRequest(BaseModel):
    status: Literal["active", "superseded", "retracted"]


class ProjectFactRelationRequest(BaseModel):
    target_fact_id: UUID
    relation_type: Literal["conflicts_with", "supersedes", "synonym_of"]


class ProjectFactRelationResponse(BaseModel):
    id: UUID
    project_id: UUID
    source_fact_id: UUID
    target_fact_id: UUID
    relation_type: Literal["conflicts_with", "supersedes", "synonym_of"]
    provenance: dict[str, object]
    created_at: datetime
