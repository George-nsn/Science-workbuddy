"""Deterministic, provider-free token budgeting helpers.

Context budgets in the project are declared in tokens (e.g. `max_context_tokens`),
while payload builders historically measured characters. These helpers give a
stable token estimate without shipping a heavy tokenizer, and provide a safe
hard-truncation primitive so oversized evidence blocks are trimmed (prefix
kept) instead of being dropped entirely.

The estimator is deliberately simple and language-aware:

- CJK characters count ~1 token each (close to GPT/Claude family behaviour);
- latin/numeric words count ~1.35 tokens each (subword overhead);
- everything else counts ~0.25 tokens.

It is a budgeting heuristic, not an exact upstream tokenizer. Deterministic
output keeps retrieval caches and audits reproducible.
"""

import re
from math import ceil
from typing import Any

_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")
_TRUNCATION_SUFFIX = "…"


def estimate_tokens(value: str) -> int:
    """Estimate the token count of ``value`` with a deterministic heuristic."""
    if not value:
        return 0
    cjk = len(_CJK_PATTERN.findall(value))
    words = len(_WORD_PATTERN.findall(value))
    remainder = len(value) - cjk - sum(len(item.group(0)) for item in _WORD_PATTERN.finditer(value))
    return max(1, ceil(cjk * 1.0 + words * 1.35 + max(remainder, 0) * 0.25))


def truncate_text_to_budget(value: str, budget_tokens: int) -> str:
    """Return the longest prefix of ``value`` within ``budget_tokens``.

    Never drops text outright: the empty string maps to the empty string, and
    anything else keeps at least a one-character prefix when the budget is tiny.
    """
    if estimate_tokens(value) <= budget_tokens:
        return value
    if budget_tokens <= 0:
        return ""
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(value[:middle]) <= budget_tokens:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        return value[:1]
    return value[:low].rstrip() + _TRUNCATION_SUFFIX


def fit_evidence_payload(
    items: list[dict[str, Any]],
    *,
    max_context_tokens: int,
    text_budget_ratio: float = 0.6,
    floor_tokens: int = 64,
) -> list[dict[str, Any]]:
    """Truncate the ``text`` field of evidence payload items to a shared budget.

    Copies every item (IDs, locators and citation metadata are never modified),
    then truncates the longest texts first so every item keeps at least a
    floor-token fragment. Returns the input unchanged when it already fits.
    """
    budget = max(2048, int(max_context_tokens * text_budget_ratio))
    copies = [dict(item) for item in items]
    texts = [str(item.get("text", "")) for item in copies]
    used = sum(estimate_tokens(text) for text in texts)
    if used <= budget:
        return copies
    order = sorted(range(len(copies)), key=lambda index: len(texts[index]), reverse=True)
    remaining = budget
    for rank, index in enumerate(order):
        share = max(floor_tokens, remaining // (len(order) - rank))
        truncated = truncate_text_to_budget(texts[index], share)
        copies[index]["text"] = truncated
        remaining -= estimate_tokens(truncated)
    return copies
