from sqlalchemy import LargeBinary

from science_buddy.config import Settings
from science_buddy.domain.enums import EvidenceDepth, EvidenceRelation
from science_buddy.infrastructure.models import ChunkEmbedding, DocumentAsset, EvidenceLink, Paper


def test_required_literature_constraints_are_declared() -> None:
    paper_constraints = {constraint.name for constraint in Paper.__table__.constraints}
    asset_constraints = {constraint.name for constraint in DocumentAsset.__table__.constraints}

    assert "uq_papers_pmid" in paper_constraints
    assert "uq_papers_doi_normalized" in paper_constraints
    assert "uq_document_assets_paper_content_hash" in asset_constraints


def test_vector_and_evidence_models_are_768_dimensional_and_traceable() -> None:
    vector_type = ChunkEmbedding.__table__.c.vector.type

    assert isinstance(vector_type, LargeBinary)
    assert Settings(_env_file=None).embedding_dimension == 768
    assert EvidenceLink.__table__.c.evidence_id.unique
    assert EvidenceDepth.FULLTEXT_XML.value == "fulltext_xml"
    assert EvidenceRelation.CONTRADICTS.value == "contradicts"
