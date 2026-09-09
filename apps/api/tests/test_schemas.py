import pytest
from pydantic import ValidationError

from science_buddy.api.schemas import (
    BrainstormSessionCreateRequest,
    LiteratureSearchRequest,
    RagCollectionCreateRequest,
)


def test_literature_search_defaults_to_both_sources() -> None:
    request = LiteratureSearchRequest(query="BRAF thyroid carcinoma")

    assert request.providers == ["pubmed", "europe_pmc", "openalex", "crossref"]
    assert request.limit == 20


def test_literature_search_rejects_blank_queries() -> None:
    with pytest.raises(ValidationError):
        LiteratureSearchRequest(query="  ")


def test_rag_collection_requires_vector_or_graph() -> None:
    with pytest.raises(ValidationError):
        RagCollectionCreateRequest(
            project_id="00000000-0000-0000-0000-000000000000",
            paper_ids=["00000000-0000-0000-0000-000000000001"],
            build_vector=False,
            build_graph=False,
        )


def test_brainstorm_defaults_to_max_reasoning_and_one_million_context() -> None:
    request = BrainstormSessionCreateRequest(
        project_id="00000000-0000-0000-0000-000000000000",
        mode="exploration",
    )

    assert request.model_depth == "max"
    assert request.max_context_tokens == 1000000
