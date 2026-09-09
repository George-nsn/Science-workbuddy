export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1";

export type SourceReference = {
  provider: "pubmed" | "europe_pmc" | "openalex" | "crossref";
  source_id: string;
};

export type LiteratureItem = {
  key: string;
  title: string;
  abstract: string | null;
  pmid: string | null;
  pmcid: string | null;
  doi: string | null;
  journal: string | null;
  publication_date: string | null;
  publication_year: number | null;
  authors: Array<{ full_name: string }>;
  publication_types: string[];
  mesh_headings: Array<{ descriptor_ui: string; label: string; is_major_topic: boolean }>;
  is_open_access: boolean;
  full_text_url: string | null;
  sources: SourceReference[];
};

export type LiteratureSearchResponse = {
  search_run_id: string;
  query: string;
  items: LiteratureItem[];
};

export type LibraryResponse = {
  project_id: string;
  project_name: string;
  papers: Array<{
    id: string;
    title: string;
    abstract: string | null;
    abstract_source: string | null;
    pmid: string | null;
    pmcid: string | null;
    doi: string | null;
    journal: string | null;
    publication_year: number | null;
    publication_types: string[];
    is_open_access: boolean;
    open_access_status: string;
    open_access_url: string | null;
    is_retracted: boolean;
    retraction_status: string;
    citation_count: number | null;
    influential_citation_count: number | null;
    journal_metric: {
      source: "openalex";
      two_year_mean_citedness: number | null;
      open_quartile: string | null;
      percentile: number | null;
      importance_score: number;
      quartile_basis: Record<string, unknown>;
      updated_date: string | null;
      note: string;
    } | null;
    quality_signals: Record<string, unknown>;
    metadata_sources: string[];
    tags: Array<{
      id: string;
      name: string;
      kind: string;
      origin: "auto" | "manual" | "brainstorm";
    }>;
    tags_manually_curated: boolean;
  }>;
  available_tags: Array<{
    id: string;
    name: string;
    kind: string;
    paper_count: number;
  }>;
};

export type RagCollection = {
  id: string;
  project_id: string;
  name: string;
  paper_count: number;
  vector_status: string;
  graph_status: string;
  vector_job_id: string | null;
  warning?: string | null;
};

export type ResearchRouting = {
  question_type: "identifier" | "comparison" | "mechanism" | "pico" | "methodology" | "systematic_review" | "latest_progress" | "general";
  strategy: "direct" | "decomposition" | "deep_research";
  confidence: number;
  reason_codes: string[];
  max_followup_rounds: number;
  version: string;
  subqueries: Array<{ subquery_id: string; focus: string; query: string }>;
};

export type RetrievalStep = {
  subquery_id: string;
  focus: string;
  query: string;
  round: number;
  candidates: number;
  cache_level: string;
  route_errors: string[];
};

export type ResearchPlan = {
  question_type: string;
  strategy: string;
  core_claims: Array<{
    claim_id: string;
    statement: string;
    claim_type: string;
    priority: number;
    falsifiable_prediction: string;
    required_evidence_types: string[];
    status: string;
    evidence_ids: string[];
    contradicting_evidence_ids: string[];
    unresolved_reason: string | null;
  }>;
  required_subqueries: string[];
  exploratory_subqueries: string[];
  counterevidence_subqueries: string[];
  allowed_sources: string[];
  allowed_actions: string[];
  max_rounds: number;
  max_total_subqueries: number;
  max_actions_per_round: number;
  max_external_requests: number;
  source: string;
  planner_error: string | null;
};

export type ResearchAction = {
  round_number: number;
  action_id: string;
  action_type: string;
  query: string;
  target_claim_ids: string[];
  rationale: string;
  expected_information_gain: number;
  novelty: number;
  falsifiability: number;
  estimated_cost: number;
  requested_source: string;
  requires_external_access: boolean;
  decision_score: number;
  approved: boolean;
  rejection_reason: string | null;
};

export type SufficiencyVerdict = {
  round_number: number;
  answer_coverage: number;
  evidence_quality: number;
  counterevidence_coverage: number;
  alternative_hypothesis_coverage: number;
  population_coverage: number;
  methodological_coverage: number;
  novelty_coverage: number;
  unresolved_claim_ids: string[];
  conflicting_claim_ids: string[];
  proposed_followups: string[];
  sufficient: boolean;
  stop_reason: string;
};

export type ResearchProgressStep = {
  step_number: number;
  round_number: number;
  step_type: string;
  decision: string;
  status: string;
};

export type ResearchResponse = {
  run_id: string;
  retrieval_mode: "identifier" | "hybrid-sparse" | "hybrid-dense";
  retrieval_version: string;
  routing: ResearchRouting | null;
  retrieval_steps: RetrievalStep[];
  tool_actions: Array<{
    tool: "local_retrieval" | "graph_local_search" | "graph_global_search" | "graph_drift_search" | "graph_path_search" | "citation_graph" | "scholarly_discovery" | "controlled_web_search";
    reason: string;
    queries: string[];
    executed: boolean;
    results: number;
    error: string | null;
  }>;
  research_plan: ResearchPlan | null;
  research_actions: ResearchAction[];
  sufficiency: SufficiencyVerdict[];
  progress: ResearchProgressStep[];
  result: {
    answer: string;
    claims: Array<{
      statement: string;
      relation: string;
      semantic_verification: string;
      evidence: Array<{
        evidence_id: string;
        text: string;
        source_locator: Record<string, unknown>;
        citation_label: string | null;
        formatted_citation: string | null;
      }>;
    }>;
    gaps: string[];
    conflicts: string[];
    followup_queries: string[];
    unverified_hypotheses: string[];
  };
};

export type ResearchTrace = {
  run_id: string;
  plan_versions: Array<{
    version_number: number;
    source: string;
    parent_version_number: number | null;
    added_claim_ids: string[];
    removed_claim_ids: string[];
    added_action_ids: string[];
    removed_action_ids: string[];
    added_queries: string[];
  }>;
  rounds: Array<{
    round_number: number;
    phase: string;
    status: string;
    approved_actions: number;
    rejected_actions: number;
    external_actions: number;
    action_budget_used: number;
    action_budget_limit: number;
    external_budget_used: number;
    external_budget_limit: number;
    new_evidence_count: number;
    sufficient: boolean | null;
    stop_reason: string | null;
  }>;
  claim_evidence_matrix: Array<{
    claim_key: string;
    statement: string;
    status: string;
    verification_label: string | null;
    verification_confidence: number | null;
    evidence_ids: string[];
    contradicting_evidence_ids: string[];
  }>;
  progress: ResearchProgressStep[];
};

export type ResearchReview = {
  run_id: string;
  markdown: string;
  verified_claim_count: number;
  citation_sentence_count: number;
};

export type LiteratureArchive = {
  id: string;
  project_id: string;
  name: string;
  paper_count: number;
  origin: "manual" | "upload_batch";
  created_at: string;
};

export type BrainstormSession = {
  id: string;
  project_id: string;
  collection_id: string | null;
  title: string;
  mode: "exploration" | "refinement";
  status: string;
  session_number: number;
  confirmation_round: number;
  allow_pubmed_search: boolean;
  model_processing_allowed: boolean;
  workflow: "classic" | "plan";
  phase: string;
  allow_web_search: boolean;
  plan_snapshot: Record<string, unknown>;
  model_depth: "quick" | "balanced" | "deep" | "max";
  max_context_tokens: number;
  agent_background: string;
  created_at: string;
};

export type UsageSummary = {
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  request_count: number;
  retrieval_count: number;
  cache_hits: number;
  cache_hit_rate: number;
  known_cost_usd: number;
  cost_coverage_rate: number;
  estimated_token_events: number;
};

export type BrainstormSessionUsage = {
  session_id: string;
  summary: UsageSummary;
  turns: Array<UsageSummary & { turn_number: number; message_id: string | null }>;
};

export type PlanDirection = {
  direction_id: string;
  title: string;
  rationale: string;
  why_hot: string[];
  novelty_points: string[];
  feasibility_notes: string[];
  key_risks: string[];
  evidence_ids: string[];
  web_sources: string[];
};

export type PreferenceQuestion = {
  question_id: string;
  question: string;
  kind: "single_choice" | "multi_choice" | "scale" | "text";
  options: string[];
  required: boolean;
  rationale: string;
};

export type PrefetchJob = {
  direction_id: string;
  status: "running" | "succeeded" | "failed";
  prefetched_paper_ids: string[];
  error?: string | null;
};

export type BrainstormPlanSnapshot = {
  session_id: string;
  phase: string;
  directions: PlanDirection[];
  preference_questions: PreferenceQuestion[];
  selected_direction_id: string | null;
  preference_profile: Record<string, unknown>;
  preference_answers: Record<string, unknown>;
  readiness_score: number;
  missing_fields: string[];
  web_search_used: boolean;
  prefetch_job?: PrefetchJob | null;
};

export type BrainstormBackgroundJob = {
  id: string;
  session_id: string;
  kind: "plan_discovery" | "plan_generation";
  status: "queued" | "running" | "retrying" | "succeeded" | "failed" | "cancelled";
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type BrainstormMessage = {
  id: string;
  sequence_number: number;
  role: string;
  agent_name: string | null;
  content: string;
  payload: Record<string, unknown>;
  evidence_ids: string[];
  created_at: string;
};

export type BrainstormVersion = {
  id: string;
  version_number: number;
  kind: string;
  parent_version_id: string | null;
  content: string;
  source_filename: string | null;
  change_summary: Array<Record<string, unknown>>;
  created_at: string;
};

export type BrainstormDetail = {
  session: BrainstormSession;
  messages: BrainstormMessage[];
  versions: BrainstormVersion[];
  memory: BrainstormSessionMemory | null;
  facts: ProjectFact[];
  usage: BrainstormSessionUsage;
};

export type BrainstormSessionMemory = {
  session_id: string;
  source_message_id: string;
  turn_number: number;
  summary_markdown: string;
  summary_data: Record<string, unknown>;
  updated_at: string;
};

export type ProjectFact = {
  id: string;
  project_id: string;
  category: string;
  statement: string;
  source_type: string;
  source_id: string;
  source_session_id: string | null;
  source_locator: Record<string, unknown>;
  confidence: number;
  importance: number;
  status: "active" | "superseded" | "retracted";
  created_at: string;
  updated_at: string;
};

export type MemoryContextType =
  | "project_fact"
  | "research_step"
  | "verified_claim"
  | "failed_route";

export type MemoryContextConsumer = "brainstorm" | "research_planner" | "synthesis";

export type MemoryContextItem = {
  memory_type: MemoryContextType;
  entity_id: string;
  role:
    | "project_context"
    | "confirmed_project_fact"
    | "planning_experience"
    | "verified_claim_prior"
    | "route_failure_experience";
  text: string;
  category: string | null;
  status: string;
  confidence: number;
  importance: number;
  fused_score: number;
  routes: string[];
  source: Record<string, unknown>;
  related: Array<Record<string, unknown>>;
  created_at: string;
  evidence_eligible: false;
};

export type MemoryContextResponse = {
  project_id: string;
  consumer: MemoryContextConsumer;
  allowed_memory_types: MemoryContextType[];
  strategy: string;
  indexed: number;
  embedding_model: string;
  evidence_boundary: string;
  items: MemoryContextItem[];
};

export type RecycleBin = {
  project_id: string;
  retention_days: number;
  items: Array<{
    entity_type: "brainstorm_session" | "research_run" | "workbench_note" | "workbench_task" | "workbench_attachment";
    entity_id: string;
    title: string;
    deleted_at: string;
    purge_after: string;
  }>;
};

export type BrainstormTurn = {
  session_id: string;
  status: string;
  version_id: string;
  version_number: number;
  response_markdown: string;
  technical_route_mermaid: string | null;
  confirmation_questions: string[];
  safety_flags: string[];
  evidence_ids: string[];
  evidence_count: number;
  auto_ingested_paper_ids: string[];
  message_id: string;
  usage: UsageSummary;
};

export type UsageDashboard = {
  project_id: string;
  period: "7d" | "30d" | "90d" | "all";
  granularity: "day" | "week" | "month";
  summary: UsageSummary;
  series: Array<UsageSummary & { bucket: string }>;
  models: Array<UsageSummary & { provider: string; model: string }>;
};

export type WorkbenchNote = {
  id: string;
  project_id: string;
  entry_date: string;
  title: string;
  content_markdown: string;
  created_at: string;
  updated_at: string;
};

export type WorkbenchTask = {
  id: string;
  project_id: string;
  work_date: string | null;
  title: string;
  status: "todo" | "in_progress" | "done";
  priority: "low" | "medium" | "high";
  completed_at: string | null;
  source_kind: string | null;
  source_id: string | null;
  source_section: string | null;
  created_at: string;
  updated_at: string;
};

export type BrainstormTaskDispatch = {
  session_id: string;
  message_id: string;
  sections_detected: number;
  created_count: number;
  skipped_existing: number;
  tasks: WorkbenchTask[];
};

export type WorkbenchAttachment = {
  id: string;
  project_id: string;
  note_id: string;
  filename: string;
  media_type: string;
  byte_size: number;
  width: number;
  height: number;
  ocr_status: "complete" | "empty" | "failed" | "skipped";
  ocr_text: string;
  ocr_error: string | null;
  attachment_kind: "uploaded_image" | "generated_plot";
  generator: string | null;
  source_table_hash: string | null;
  source_table_markdown: string | null;
  render_spec: Record<string, unknown>;
  render_revision: number;
  caption: string | null;
  content_url: string;
  created_at: string;
};

export type WorkbenchPlotStyle = {
  chart_type: "auto" | "bar" | "line" | "scatter" | "distribution" | "heatmap";
  title: string;
  caption: string;
  intent: string;
  allow_model_planning: boolean;
  font_size: number;
  palette: "journal" | "npg" | "nejm" | "lancet" | "jama" | "colorblind";
  line_width: number;
  point_size: number;
  figure_width: number;
  figure_height: number;
  dpi: number;
  show_grid: boolean;
  legend_position: "best" | "top" | "bottom" | "left" | "right" | "none";
  data_layout: "auto" | "long" | "wide" | "summary";
  condition_column: string;
  value_column: string;
  replicate_column: string;
  replicate_columns: string[];
  summary_stat: "mean_sd" | "mean_sem" | "mean_ci95";
  show_all_points: boolean;
  show_sample_size: boolean;
  replicate_unit: "biological" | "technical";
  pairing_mode: "independent" | "paired";
};

export type WorkbenchPlotResponse = {
  attachment: WorkbenchAttachment;
  chart_type: Exclude<WorkbenchPlotStyle["chart_type"], "auto">;
  rationale: string;
  detected_columns: Record<string, "numeric" | "categorical" | "temporal">;
  warnings: string[];
  markdown_image: string;
  planning_source: "deterministic" | "model";
  model_rationale: string | null;
};

export type WorkbenchOverview = {
  project_id: string;
  project_name: string;
  month: string;
  days: Array<{
    entry_date: string;
    has_note: boolean;
    note_title: string | null;
    tasks_total: number;
    tasks_done: number;
  }>;
  tasks: WorkbenchTask[];
  recent_notes: WorkbenchNote[];
};

export type SynthesisSource = {
  id: string;
  source_type: "workbench_note" | "uploaded_file" | "user_text";
  source_ref_id: string | null;
  filename: string;
  media_type: string;
  content_hash: string;
  module_count: number;
  summary: string;
  relations: Array<Record<string, unknown>>;
  status: string;
  error_message: string | null;
  created_at: string;
};

export type SynthesisSection = {
  id: string;
  parent_id: string | null;
  section_key: string;
  ordinal: number;
  level: number;
  title: string;
  purpose: string;
  outline: string[];
  draft_markdown: string;
  section_summary: string;
  source_ids: string[];
  evidence_ids: string[];
  status: string;
  revision: number;
  updated_at: string;
};

export type SynthesisReview = {
  id: string;
  section_id: string | null;
  round_number: number;
  scope: string;
  verdict: string;
  feedback: Record<string, unknown>;
  affected_section_keys: string[];
  created_at: string;
};

export type SynthesisAgentRun = {
  id: string;
  section_id: string | null;
  round_number: number;
  agent_name: string;
  output: Record<string, unknown>;
  status: string;
  error: string | null;
  created_at: string;
};

export type SynthesisSession = {
  id: string;
  project_id: string;
  title: string;
  topic: string;
  status: "draft" | "analyzing" | "writing" | "reviewing" | "completed" | "failed";
  model_depth: "quick" | "balanced" | "deep" | "max";
  max_context_tokens: number;
  max_review_rounds: number;
  include_workbench_notes: boolean;
  allow_online_literature: boolean;
  current_round: number;
  section_count: number;
  source_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type SynthesisDetail = SynthesisSession & {
  template_profile: Record<string, unknown>;
  document_map: Record<string, unknown>;
  global_outline: Array<Record<string, unknown>>;
  global_summary: string;
  glossary: Record<string, unknown>;
  citation_ledger: Record<string, unknown>;
  figure_manifest: Array<Record<string, unknown>>;
  manuscript_markdown: string;
  sources: SynthesisSource[];
  sections: SynthesisSection[];
  reviews: SynthesisReview[];
  agent_runs: SynthesisAgentRun[];
};

export type SynthesisJob = {
  id: string;
  session_id: string;
  status: "queued" | "running" | "retrying" | "succeeded" | "failed" | "cancelled";
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkbenchDay = {
  project_id: string;
  entry_date: string;
  note: WorkbenchNote | null;
  tasks: WorkbenchTask[];
  attachments: WorkbenchAttachment[];
};

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch (caught) {
    const reason = caught instanceof Error ? caught.message : "network request failed";
    throw new Error(
      `无法连接后端 API（${API_URL}）：${reason}。请确认 API 已启动且前端地址在 CORS 允许范围内。`,
    );
  }
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // Preserve the HTTP status fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
