import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from science_buddy.services.literature.common import normalize_doi

_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_PMID_PATTERN = re.compile(r"^(?:pmid\s*[:：]?\s*)?(\d{5,10})$", re.IGNORECASE)
_PMCID_PATTERN = re.compile(r"^(?:pmcid\s*[:：]?\s*)?(PMC\d+)$", re.IGNORECASE)
_ASCII_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-./][A-Za-z0-9]+)*")
_IGNORED_TERMS = {"and", "or", "not", "the", "of", "in", "on", "for", "with"}

# Deterministic seed aliases for universal scientific and methodological concepts.
# Subject taxonomy labels (ACM CCS, MeSH, PACS) provide authoritative domain routes;
# this list ensures lexical fallback across computing, physical, engineering, and natural sciences.
_SCIENTIFIC_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        {
            # Computer Science & AI
            "检索增强生成": "retrieval augmented generation",
            "大语言模型": "large language model",
            "强化学习": "reinforcement learning",
            "深度学习": "deep learning",
            "神经网络": "neural network",
            "知识图谱": "knowledge graph",
            "自注意力": "self attention",
            "状态空间模型": "state space model",
            "图神经网络": "graph neural network",
            "预训练模型": "pretrained model",
            "微调": "fine tuning",
            "提示工程": "prompt engineering",
            "因果推断": "causal inference",
            "目标检测": "object detection",
            "自然语言处理": "natural language processing",
            # Universal Methodology & Scientific Analysis
            "基准测试": "benchmark",
            "消融实验": "ablation study",
            "统计显著性": "statistical significance",
            "置信区间": "confidence interval",
            "误差界限": "error bound",
            "计算复杂度": "computational complexity",
            "经验分析": "empirical analysis",
            "对照试验": "controlled experiment",
            "随机对照试验": "randomized controlled trial",
            "系统综述": "systematic review",
            "荟萃分析": "meta analysis",
            "蒙特卡洛": "monte carlo",
            "优化算法": "optimization algorithm",
            "收敛性": "convergence",
            "超参数": "hyperparameter",
            "可解释性": "interpretability",
            "泛化性": "generalization",
            "可重复性": "reproducibility",
            # Physics & Chemistry & Materials
            "密度泛函": "density functional theory",
            "分子动力学": "molecular dynamics",
            "晶体结构": "crystal structure",
            "材料合成": "materials synthesis",
            "相变": "phase transition",
            # Natural & Biomedical Sciences
            "乳头状甲状腺癌": "papillary thyroid carcinoma",
            "甲状腺癌": "thyroid carcinoma",
            "肺腺癌": "lung adenocarcinoma",
            "非小细胞肺癌": "non small cell lung cancer",
            "结直肠癌": "colorectal cancer",
            "乳腺癌": "breast cancer",
            "临床试验": "clinical trial",
            "生物标志物": "biomarker",
            "不良反应": "adverse event",
            "总体生存": "overall survival",
            "无进展生存": "progression free survival",
            "基因突变": "gene mutation",
            "免疫治疗": "immunotherapy",
            "靶向治疗": "targeted therapy",
            "预后": "prognosis",
            "治疗": "treatment",
            "机制": "mechanism",
            "突变": "mutation",
            "表达": "expression",
            "疗效": "efficacy",
            "安全性": "safety",
            "生存": "survival",
            "风险": "risk",
            "患者": "patients",
            "疾病": "disease",
            "基因": "gene",
            "蛋白": "protein",
            "药物": "drug",
            "细胞": "cell",
            "免疫": "immune",
            "炎症": "inflammation",
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_BIOMEDICAL_ALIASES = _SCIENTIFIC_ALIASES


@dataclass(frozen=True, slots=True)
class QueryExpansion:
    source: str
    target: str
    authority: str = "built-in-biomedical-alias-v1"


@dataclass(frozen=True, slots=True)
class QueryPlan:
    original_query: str
    language: Literal["zh", "en", "mixed"]
    identifier_kind: Literal["pmid", "pmcid", "doi"] | None
    identifier_value: str | None
    english_query: str | None
    simple_query: str | None
    dense_queries: tuple[tuple[str, str], ...]
    expansions: tuple[QueryExpansion, ...]
    routing_context: str = "direct"
    research_question_type: str = "general"
    query_focus: str = "original"
    parent_query: str | None = None
    subquery_id: str | None = None
    version: str = "query-plan-v1"

    @property
    def is_identifier_lookup(self) -> bool:
        return self.identifier_kind is not None


class DeterministicQueryPlanner:
    """Plan bilingual retrieval without requiring a cloud model or external query service."""

    def plan(self, query: str) -> QueryPlan:
        original = " ".join(query.strip().split())
        if not original:
            raise ValueError("query must not be blank")
        identifier_kind, identifier_value = self._identifier(original)
        has_cjk = bool(_CJK_PATTERN.search(original))
        has_ascii = bool(_ASCII_TERM_PATTERN.search(original))
        language: Literal["zh", "en", "mixed"]
        if has_cjk and has_ascii:
            language = "mixed"
        elif has_cjk:
            language = "zh"
        else:
            language = "en"

        english_query, expansions = self._english_query(original, language)
        simple_terms = self._simple_terms(original, english_query)
        simple_query = " ".join(simple_terms) or None
        dense_queries: list[tuple[str, str]] = [("dense_original", original)]
        if english_query and english_query.casefold() != original.casefold():
            dense_queries.append(("dense_translated", english_query))
        return QueryPlan(
            original_query=original,
            language=language,
            identifier_kind=identifier_kind,
            identifier_value=identifier_value,
            english_query=english_query,
            simple_query=simple_query,
            dense_queries=tuple(dense_queries),
            expansions=tuple(expansions),
        )

    @staticmethod
    def _identifier(
        query: str,
    ) -> tuple[Literal["pmid", "pmcid", "doi"] | None, str | None]:
        if match := _PMCID_PATTERN.fullmatch(query):
            return "pmcid", match.group(1).upper()
        if match := _PMID_PATTERN.fullmatch(query):
            return "pmid", match.group(1)
        doi = normalize_doi(query)
        if doi and doi.startswith("10.") and "/" in doi and " " not in doi:
            return "doi", doi
        return None, None

    @staticmethod
    def _english_query(
        query: str, language: Literal["zh", "en", "mixed"]
    ) -> tuple[str | None, list[QueryExpansion]]:
        if language == "en":
            return query, []
        remaining = query
        translated_terms: list[str] = []
        expansions: list[QueryExpansion] = []
        for source, target in _BIOMEDICAL_ALIASES:
            if source not in remaining:
                continue
            translated_terms.append(target)
            expansions.append(QueryExpansion(source=source, target=target))
            remaining = remaining.replace(source, " ")
        ascii_terms = [
            term
            for term in _ASCII_TERM_PATTERN.findall(remaining)
            if term.casefold() not in _IGNORED_TERMS
        ]
        values = list(dict.fromkeys([*ascii_terms, *translated_terms]))
        return " ".join(values) or None, expansions

    @staticmethod
    def _simple_terms(original: str, english_query: str | None) -> list[str]:
        source = f"{original} {english_query or ''}"
        return list(
            dict.fromkeys(
                term
                for term in _ASCII_TERM_PATTERN.findall(source)
                if term.casefold() not in _IGNORED_TERMS and len(term) > 1
            )
        )


def _common_prefix(left: str, right: str) -> int:
    length = 0
    for left_char, right_char in zip(left, right, strict=False):
        if left_char != right_char:
            break
        length += 1
    return length


def _token_overlap(left: str, right: str) -> int:
    """Count tokens shared by two strings (exact or common-prefix match >= 4)."""
    left_tokens = {token.casefold() for token in left.split() if len(token) >= 4}
    right_tokens = {token.casefold() for token in right.split() if len(token) >= 4}
    overlap = 0
    for left_token in left_tokens:
        for right_token in right_tokens:
            if left_token == right_token or _common_prefix(left_token, right_token) >= 4:
                overlap += 1
    return overlap


def expand_plan_with_mesh(
    plan: QueryPlan,
    mesh_labels: Sequence[str],
    *,
    max_terms: int = 4,
) -> QueryPlan:
    """Ground a zh/mixed plan in the project's own authoritative MeSH labels.

    Only labels that lexically overlap the already-translated English query are
    considered, so expansion stays anchored to the user's question instead of
    polluting it with unrelated project terminology. Added labels extend the
    English FTS/metadata/MeSH routes and receive a dedicated dense query.
    """
    if plan.language == "en" or not plan.english_query or not mesh_labels:
        return plan
    normalized = [
        label
        for label in dict.fromkeys(str(value).strip() for value in mesh_labels)
        if label
    ]
    if not normalized:
        return plan
    existing_terms = {term.casefold() for term in plan.english_query.split() if len(term) >= 4}
    candidates: list[tuple[str, int]] = []
    for label in normalized:
        overlap = _token_overlap(plan.english_query, label)
        new_tokens = [token for token in label.split() if token.casefold() not in existing_terms]
        if overlap > 0 and new_tokens:
            candidates.append((label, overlap))
    if not candidates:
        return plan
    candidates.sort(key=lambda item: (-item[1], item[0].casefold()))
    added_labels: list[str] = []
    expansions: list[QueryExpansion] = []
    for label, _ in candidates:
        if len(added_labels) >= max_terms:
            break
        new_tokens = [
            token for token in label.split() if token.casefold() not in existing_terms
        ]
        if not new_tokens:
            continue
        expansions.append(
            QueryExpansion(source=f"mesh:{label}", target=label, authority="project-mesh-v1")
        )
        existing_terms.update(token.casefold() for token in label.split())
        added_labels.append(label)
    if not expansions:
        return plan
    english_query = " ".join([plan.english_query, *added_labels])
    dense_queries = [
        *plan.dense_queries,
        ("dense_mesh_expanded", " ".join(added_labels)),
    ]
    return replace(
        plan,
        english_query=english_query,
        dense_queries=tuple(dense_queries),
        expansions=tuple([*plan.expansions, *expansions]),
    )
