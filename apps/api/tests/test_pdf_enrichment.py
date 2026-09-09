from science_buddy.domain.contracts import SourceSegment
from science_buddy.services.documents import ParsedSection
from science_buddy.services.pdf_enrichment import extract_pdf_enrichment


def section(text: str) -> ParsedSection:
    return ParsedSection(
        title="Page 1",
        section_path="Page 1",
        ordinal=0,
        segments=(SourceSegment(text, "Page 1", 1, 1, 0, len(text)),),
    )


def test_pdf_enrichment_extracts_explicit_abstract_and_keywords() -> None:
    text = """
    Exploring the diversity of anti-defense systems
    ABSTRACT
    Anti-defense systems allow bacteriophages and mobile genetic elements to evade
    bacterial defense systems. We developed a comparative genomics workflow to map
    these systems across prokaryotes and phages. The results reveal substantial
    diversity and provide a resource for bioinformatics investigation.
    Keywords: anti-defense systems; bacteriophages; mobile genetic elements;
    comparative genomics; bioinformatics
    1 Introduction
    Bacteria encode diverse defense systems.
    """

    value = extract_pdf_enrichment([section(text)])

    assert value.summary.startswith("Anti-defense systems allow")
    assert "provide a resource" in value.summary
    assert value.summary_kind == "local_extractive_pdf"
    assert set(value.keywords) >= {
        "anti-defense systems",
        "bacteriophages",
        "mobile genetic elements",
        "comparative genomics",
        "bioinformatics",
    }


def test_pdf_enrichment_falls_back_to_evidence_like_sentences_and_domain_terms() -> None:
    text = (
        "Exploring phage biology. "
        "This study presents a machine learning method for identifying anti-defense "
        "systems from protein sequences across prokaryotes and bacteriophages. "
        "Our results demonstrate that comparative genomics improves the discovery "
        "of mobile genetic elements in diverse bacterial genomes. "
        "Additional implementation details follow in the Methods section."
    )

    value = extract_pdf_enrichment([section(text)])

    assert "This study presents" in value.summary
    assert set(value.keywords) >= {
        "Anti-defense systems",
        "Mobile genetic elements",
        "Bacteriophages",
        "Machine learning",
        "Comparative genomics",
        "Prokaryotes",
    }
