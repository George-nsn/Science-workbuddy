from dataclasses import dataclass
from typing import Any, Literal

CostClass = Literal["local", "free_external", "metered_external"]
ImplementationStatus = Literal["available", "registered_only"]


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    tool_id: str
    label: str
    provider: str
    input_schema: dict[str, Any]
    permission: str
    cost_class: CostClass
    timeout_seconds: int
    evidence_eligible: bool
    mechanical_verification: str
    enabled: bool
    implementation_status: ImplementationStatus
    action_policy_eligible: bool = False


QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["query"],
    "properties": {"query": {"type": "string", "maxLength": 500}},
}

TOOLS = (
    ToolRegistration(
        "local_hybrid_search",
        "本地混合检索",
        "science_buddy",
        QUERY_SCHEMA,
        "project_read",
        "local",
        30,
        True,
        "Evidence ID HMAC + project/collection candidate membership",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "graph_local_search",
        "知识图谱局部检索",
        "science_buddy",
        QUERY_SCHEMA,
        "project_read",
        "local",
        30,
        True,
        "Evidence ID HMAC + graph provenance resolves to source chunks",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "graph_global_search",
        "知识图谱全局社区检索",
        "science_buddy",
        QUERY_SCHEMA,
        "project_read",
        "local",
        30,
        True,
        "Evidence ID HMAC + community report resolves to source chunks",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "graph_drift_search",
        "知识图谱漂移检索",
        "science_buddy",
        QUERY_SCHEMA,
        "project_read",
        "local",
        30,
        True,
        "Evidence ID HMAC + DRIFT routes resolve to source chunks",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "graph_path_search",
        "知识图谱路径检索",
        "science_buddy",
        QUERY_SCHEMA,
        "project_read",
        "local",
        30,
        True,
        "Evidence ID HMAC + path audit resolves to source chunks",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "citation_landscape",
        "引文网络检索",
        "science_buddy",
        QUERY_SCHEMA,
        "project_read",
        "local",
        30,
        True,
        "Evidence ID HMAC + citation neighborhood resolves to source chunks",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "scholarly_discovery",
        "学术源受控发现（PubMed/EPMC/OpenAlex/Crossref）",
        "science_buddy",
        QUERY_SCHEMA,
        "external_scholarly_discovery",
        "free_external",
        30,
        True,
        "Adapter snapshot + deduplicated ingestion + Evidence ID membership",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "controlled_web_search",
        "受控网络搜索（Tavily）",
        "Tavily",
        QUERY_SCHEMA,
        "external_web_search",
        "metered_external",
        30,
        True,
        "HTTPS-only clipped results + per-session authorization audit",
        True,
        "available",
        True,
    ),
    ToolRegistration(
        "workbench_plot_agent",
        "科研笔记表格绘图 Agent",
        "science_buddy",
        {
            "type": "object",
            "required": ["markdown_table"],
            "properties": {
                "markdown_table": {"type": "string", "maxLength": 200000},
                "intent": {"type": "string", "maxLength": 2000},
                "allow_model_planning": {"type": "boolean"},
                "render_style": {"type": "object"},
            },
        },
        "workbench_note_write",
        "local",
        90,
        False,
        "Source table hash + declarative render spec + local PNG revision",
        True,
        "available",
    ),
    ToolRegistration(
        "clinicaltrials_gov",
        "ClinicalTrials.gov",
        "NIH",
        {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "page_size": {"type": "integer", "maximum": 100},
            },
        },
        "external_clinical_trials",
        "free_external",
        20,
        True,
        "NCT identifier + API response snapshot + field-level locator",
        False,
        "registered_only",
    ),
    ToolRegistration(
        "blast",
        "NCBI BLAST",
        "NCBI",
        {
            "type": "object",
            "required": ["sequence", "program", "database"],
            "properties": {
                "sequence": {"type": "string", "maxLength": 100000},
                "program": {"enum": ["blastn", "blastp", "blastx"]},
                "database": {"type": "string"},
            },
        },
        "external_sequence_search",
        "free_external",
        120,
        True,
        "RID + query hash + accession/e-value/alignment locator",
        False,
        "registered_only",
    ),
    ToolRegistration(
        "oncokb",
        "OncoKB",
        "Memorial Sloan Kettering",
        {
            "type": "object",
            "required": ["gene"],
            "properties": {
                "gene": {"type": "string"},
                "alteration": {"type": "string"},
                "tumor_type": {"type": "string"},
            },
        },
        "external_oncology_knowledgebase",
        "metered_external",
        20,
        True,
        "OncoKB version + query tuple + annotation fields",
        False,
        "registered_only",
    ),
    ToolRegistration(
        "bio_tools",
        "bio.tools registry",
        "ELIXIR",
        QUERY_SCHEMA,
        "external_tool_discovery",
        "free_external",
        20,
        False,
        "bio.tools identifier + registry response snapshot",
        False,
        "registered_only",
    ),
    ToolRegistration(
        "generic_mcp",
        "通用 MCP",
        "user_configured",
        {
            "type": "object",
            "required": ["server_id", "tool_name", "arguments"],
            "properties": {
                "server_id": {"type": "string"},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object"},
            },
        },
        "explicit_mcp_tool_grant",
        "metered_external",
        30,
        False,
        "Disabled until a per-tool verifier is registered",
        False,
        "registered_only",
    ),
)


RESEARCH_ACTION_TOOL_IDS: frozenset[str] = frozenset(
    item.tool_id
    for item in TOOLS
    if item.implementation_status == "available" and item.action_policy_eligible
)
"""Single source of truth for capabilities executable through the action policy.

`dynamic_research._ALLOWED_ACTION_TYPES` and the claim-worker allowlist both
derive from this set (plus the `stop_research` control-flow signal), so the
tool catalog and the execution whitelist cannot drift apart. Keep ActionType
in sync when adding tools: tests/test_research_trace_facts_tools.py asserts the
Literal matches this registry. Tools available through their own surfaces
(e.g. the workbench plot agent) are excluded via `action_policy_eligible`.
"""


def list_tool_registrations() -> tuple[ToolRegistration, ...]:
    return TOOLS


def get_enabled_tool(tool_id: str) -> ToolRegistration:
    value = next((item for item in TOOLS if item.tool_id == tool_id), None)
    if value is None:
        raise LookupError("Tool is not registered")
    if not value.enabled or value.implementation_status != "available":
        raise PermissionError("Tool is registered but not enabled for execution")
    return value
