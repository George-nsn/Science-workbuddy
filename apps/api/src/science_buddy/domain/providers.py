from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class AuthorRecord(BaseModel):
    full_name: str
    family_name: str | None = None
    given_name: str | None = None
    collective_name: str | None = None


class MeSHHeadingRecord(BaseModel):
    descriptor_ui: str
    label: str
    is_major_topic: bool = False


class LiteratureRecord(BaseModel):
    provider: str
    source_id: str
    title: str
    abstract: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    journal: str | None = None
    publication_date: date | None = None
    publication_year: int | None = None
    authors: list[AuthorRecord] = Field(default_factory=list)
    publication_types: list[str] = Field(default_factory=list)
    mesh_headings: list[MeSHHeadingRecord] = Field(default_factory=list)
    is_open_access: bool = False
    full_text_url: str | None = None
    open_access_status: str = "unknown"
    is_retracted: bool = False
    retraction_status: str = "unknown"
    citation_count: int | None = None
    influential_citation_count: int | None = None
    quality_signals: dict[str, Any] = Field(default_factory=dict)
    external_metadata: dict[str, Any] = Field(default_factory=dict)
    metadata_sources: list[str] = Field(default_factory=list)


class LiteratureProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int) -> list[LiteratureRecord]: ...

    async def fetch(self, source_id: str) -> LiteratureRecord: ...

    async def fetch_many(self, source_ids: Sequence[str]) -> list[LiteratureRecord]: ...


class StructuredGenerationRequest(BaseModel):
    system_instruction: str
    context_instruction: str | None = None
    user_content: str
    response_schema: dict[str, Any]
    operation: str = "structured_generation"
    depth: Literal["quick", "balanced", "deep", "max"] = "balanced"
    max_context_tokens: int = Field(default=65536, ge=8192, le=1000000)
    max_output_tokens: int | None = Field(default=None, ge=512, le=393216)


class TextGenerationRequest(BaseModel):
    system_instruction: str
    context_instruction: str | None = None
    user_content: str
    operation: str = "text_generation"
    depth: Literal["quick", "balanced", "deep", "max"] = "balanced"
    max_context_tokens: int = Field(default=65536, ge=8192, le=1000000)
    max_output_tokens: int | None = Field(default=None, ge=512, le=393216)


class ModelCallUsage(BaseModel):
    operation: str
    provider: str
    model: str
    depth: Literal["quick", "balanced", "deep", "max"] = "balanced"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    token_count_estimated: bool = False
    cost_usd: float | None = None
    cost_source: Literal["upstream", "unavailable"] = "unavailable"
    latency_ms: float = Field(default=0.0, ge=0)


class ModelProvider(Protocol):
    name: str

    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> dict[str, Any]: ...

    async def generate_text(self, request: TextGenerationRequest) -> str: ...

    def stream_text(self, system: str, messages: Sequence[str]) -> AsyncIterator[str]: ...
