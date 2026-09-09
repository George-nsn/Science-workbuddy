import pytest
from pydantic import ValidationError

from science_buddy.config import Settings


def test_default_embedding_contract() -> None:
    settings = Settings(_env_file=None)

    assert settings.embedding_model == "intfloat/multilingual-e5-base"
    assert settings.embedding_dimension == 768
    assert settings.max_refinement_rounds == 2


def test_initial_schema_rejects_other_vector_dimensions() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedding_dimension=1024)
