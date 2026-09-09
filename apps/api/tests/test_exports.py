from io import BytesIO

from docx import Document

from science_buddy.services.exports import (
    research_result_bibtex,
    research_result_docx,
    research_result_markdown,
    research_result_ris,
)

RESULT = {
    "answer": "A supported answer.",
    "claims": [
        {
            "statement": "A supported claim.",
            "evidence": [
                {
                    "text": "Source evidence.",
                    "source_locator": {"pmid": "123", "section_path": "Results"},
                }
            ],
        }
    ],
    "gaps": ["Long-term evidence is missing."],
    "conflicts": [],
}

PAPERS = [
    {
        "pmid": "123",
        "doi": "10.1000/trace.001",
        "title": "Traceable evidence in thyroid carcinoma",
        "journal": "Traceable Medicine",
        "publication_year": 2025,
        "authors": [
            {"family_name": "Smith", "given_name": "Jane"},
            {"full_name": "Li Wei"},
        ],
    },
    {
        "pmid": None,
        "doi": "10.1000/trace.002",
        "title": "Second paper",
        "journal": "Another Journal",
        "publication_year": None,
        "authors": [],
    },
]


def test_markdown_export_contains_traceability_and_boundary() -> None:
    content = research_result_markdown("Research question?", RESULT)

    assert "PMID" not in content or "123" in content
    assert "Not for diagnosis" in content
    assert "Source evidence." in content


def test_docx_export_is_a_readable_document() -> None:
    content = research_result_docx("Research question?", RESULT)
    document = Document(BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert "Science Buddy Evidence Report" in text
    assert "A supported claim." in text


def test_ris_export_contains_records_with_identifiers_and_authors() -> None:
    content = research_result_ris("Question?", RESULT, PAPERS)

    assert "TY  - JOUR" in content
    assert "TI  - Traceable evidence in thyroid carcinoma" in content
    assert "AU  - Smith, Jane" in content
    assert "AU  - Li Wei" in content
    assert "JO  - Traceable Medicine" in content
    assert "PY  - 2025" in content
    assert "DO  - 10.1000/trace.001" in content
    assert "AN  - 123" in content
    assert content.count("ER  -") == 2
    assert content.strip().endswith("ER  -")


def test_bibtex_export_builds_entries_with_stable_keys() -> None:
    content = research_result_bibtex("Question?", RESULT, PAPERS)

    assert "@article{sb_123," in content
    assert "author = {Smith, Jane; Li Wei}," in content
    assert "title = {{Traceable evidence in thyroid carcinoma}}," in content
    assert "journal = {Traceable Medicine}," in content
    assert "year = {2025}," in content
    assert "doi = {10.1000/trace.001}," in content
    assert "pmid = {123}," in content
    assert "@article{sb_101000trace002," in content
    assert "year = {" not in content.split("@article{sb_101000trace002,")[1]
