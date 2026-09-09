from science_buddy.services.token_budget import (
    estimate_tokens,
    fit_evidence_payload,
    truncate_text_to_budget,
)


def test_estimate_tokens_is_deterministic_and_language_aware() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("hello world") == estimate_tokens("hello world")
    # CJK characters are counted roughly one token each.
    assert estimate_tokens("甲状腺癌") >= 4
    # A CJK string should estimate at least as many tokens as characters.
    assert estimate_tokens("甲状腺癌免疫治疗") >= 8


def test_truncate_text_to_budget_keeps_prefix_within_budget() -> None:
    long_text = "word " * 500
    truncated = truncate_text_to_budget(long_text, budget_tokens=120)
    assert len(truncated) < len(long_text)
    assert estimate_tokens(truncated) <= 120
    assert long_text.startswith(truncated.removesuffix("…"))


def test_truncate_text_to_budget_returns_input_when_it_fits() -> None:
    text = "short text"
    assert truncate_text_to_budget(text, budget_tokens=1000) == text
    assert truncate_text_to_budget("x", budget_tokens=0) == ""
    assert truncate_text_to_budget("x", budget_tokens=1) == "x"


def test_fit_evidence_payload_preserves_fields_and_caps_total_budget() -> None:
    payload = [
        {
            "evidence_id": "ev1.aaa",
            "text": "sentence " * 400,
            "source_locator": {"section_path": "Abstract"},
        },
        {
            "evidence_id": "ev1.bbb",
            "text": "other " * 400,
            "source_locator": {"section_path": "Results"},
        },
    ]
    fitted = fit_evidence_payload(payload, max_context_tokens=8192)

    assert fitted[0]["evidence_id"] == "ev1.aaa"
    assert fitted[1]["evidence_id"] == "ev1.bbb"
    assert fitted[0]["source_locator"] == {"section_path": "Abstract"}
    total = sum(estimate_tokens(str(item["text"])) for item in fitted)
    assert total <= 8192
    assert all(len(str(item["text"])) > 0 for item in fitted)


def test_fit_evidence_payload_returns_unchanged_when_within_budget() -> None:
    payload = [
        {"evidence_id": "ev1.aaa", "text": "small evidence", "source_locator": {}},
        {"evidence_id": "ev1.bbb", "text": "small evidence two", "source_locator": {}},
    ]
    fitted = fit_evidence_payload(payload, max_context_tokens=65536)
    assert fitted == payload
