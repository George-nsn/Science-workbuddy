from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Science Buddy API"
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"

    database_url: str = "sqlite+aiosqlite:///data/science_buddy.db"
    redis_url: str = "redis://127.0.0.1:6379/0"

    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_dimension: int = 768
    max_refinement_rounds: int = Field(default=2, ge=0, le=2)
    claim_entailment_threshold: float = Field(default=0.72, ge=0.5, le=0.99)

    retrieval_version: str = "hybrid-v2"
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_weight_exact: float = Field(default=1.5, ge=0, le=5)
    retrieval_weight_simple: float = Field(default=1.1, ge=0, le=5)
    retrieval_weight_dense_original: float = Field(default=1.0, ge=0, le=5)
    retrieval_weight_fts_english: float = Field(default=0.9, ge=0, le=5)
    retrieval_weight_dense_translated: float = Field(default=0.8, ge=0, le=5)
    retrieval_weight_metadata: float = Field(default=0.8, ge=0, le=5)
    retrieval_weight_mesh: float = Field(default=0.8, ge=0, le=5)
    retrieval_weight_graph: float = Field(default=0.3, ge=0, le=1)
    retrieval_weight_graph_global: float = Field(default=0.15, ge=0, le=1)
    retrieval_weight_graph_drift: float = Field(default=0.2, ge=0, le=1)
    retrieval_weight_graph_path: float = Field(default=0.15, ge=0, le=1)
    retrieval_top_k_dense: int = Field(default=50, ge=1, le=200)
    retrieval_top_k_fts: int = Field(default=50, ge=1, le=200)
    retrieval_top_k_simple: int = Field(default=30, ge=1, le=200)
    retrieval_top_k_metadata: int = Field(default=30, ge=1, le=200)
    retrieval_fused_pool: int = Field(default=40, ge=1, le=200)
    retrieval_max_chunks_per_paper: int = Field(default=3, ge=1, le=20)
    retrieval_context_radius: int = Field(default=1, ge=0, le=2)
    retrieval_context_max_chars: int = Field(default=16000, ge=1000, le=100000)
    retrieval_route_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    retrieval_dense_route_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    retrieval_graph_seed_papers: int = Field(default=8, ge=0, le=20)
    retrieval_graph_neighbors: int = Field(default=8, ge=0, le=30)
    retrieval_graph_hops: int = Field(default=2, ge=1, le=3)
    retrieval_graph_community_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_graph_path_top_k: int = Field(default=3, ge=1, le=10)
    retrieval_reranker_enabled: bool = False
    retrieval_reranker_model: str = "BAAI/bge-reranker-v2-m3"
    retrieval_reranker_candidates: int = Field(default=30, ge=5, le=100)
    retrieval_reranker_weight: float = Field(default=0.65, ge=0, le=1)
    retrieval_reranker_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    retrieval_query_mesh_expansion: bool = True
    retrieval_query_mesh_max_terms: int = Field(default=4, ge=1, le=8)
    retrieval_vector_backend: str = "numpy"
    retrieval_subquery_parallelism: int = Field(default=1, ge=1, le=4)
    retrieval_neighbor_query_filter: bool = True
    retrieval_neighbor_max_per_anchor: int = Field(default=2, ge=0, le=4)
    retrieval_neighbor_min_score: float = Field(default=0.02, ge=0, le=1)
    retrieval_exclude_retracted: bool = True
    retrieval_journal_prior_weight: float = Field(default=0.1, ge=0, le=0.1)
    cache_namespace: str = "science-buddy"
    cache_l1_max_entries: int = Field(default=256, ge=16, le=4096)
    cache_l1_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    cache_l2_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    cache_l3_ttl_seconds: int = Field(default=86400, ge=300, le=604800)

    ncbi_tool: str = "science_buddy"
    ncbi_email: str | None = None
    ncbi_api_key: SecretStr | None = None
    ncbi_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    europe_pmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    crossref_base_url: str = "https://api.crossref.org"
    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key: SecretStr | None = None
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_api_key: SecretStr | None = None
    unpaywall_base_url: str = "https://api.unpaywall.org/v2"
    unpaywall_email: str | None = None
    literature_request_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    research_literature_target: int = Field(default=30, ge=10, le=100)
    research_literature_queries: int = Field(default=3, ge=1, le=6)
    default_project_name: str = "My Research Library"
    evidence_signing_key: SecretStr = SecretStr("local-development-only-change-me")
    model_config_encryption_key: SecretStr | None = None
    upload_directory: Path = Path("data/uploads")
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_batch_upload_files: int = Field(default=100, ge=1, le=500)
    pdf_parser_backend: str = "auto"
    grobid_base_url: str = "http://127.0.0.1:8070"
    document_parser_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None

    web_search_provider: str | None = None
    web_search_base_url: str = "https://api.tavily.com"
    web_search_api_key: SecretStr | None = None
    web_search_max_results: int = Field(default=5, ge=1, le=10)
    web_search_depth: str = "advanced"

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if value != 768:
            raise ValueError("The initial database migration requires 768-dimensional vectors")
        return value

    @field_validator("pdf_parser_backend")
    @classmethod
    def validate_pdf_parser_backend(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"auto", "pymupdf", "docling", "grobid"}:
            raise ValueError("pdf_parser_backend must be auto, pymupdf, docling, or grobid")
        return normalized

    @field_validator("retrieval_vector_backend")
    @classmethod
    def validate_vector_backend(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"numpy", "sqlite-numpy-float32-v1"}:
            raise ValueError("retrieval_vector_backend must be numpy")
        return normalized

    @field_validator("evidence_signing_key")
    @classmethod
    def validate_evidence_signing_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 16:
            raise ValueError("evidence_signing_key must contain at least 16 characters")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
