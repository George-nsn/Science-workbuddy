from __future__ import annotations

from typing import Any

# Aggregated from D:/zhaoxin2/literature/毕设合集 on 2026-08-11.
# Only structural statistics and hashes are retained; no thesis body text is stored.
DISTILLED_THESIS_PROFILE: dict[str, Any] = {
    "profile_version": "life-science-thesis-structure-v1",
    "source_root_hash": "6ba10c981ed1f0e5a9bd086b16bda727415b7291fab865ad1f7ce9a9cff48a7a",
    "files_found": 19,
    "unique_documents": 16,
    "duplicates_removed": 3,
    "parse_errors": 0,
    "section_coverage": {
        "abstract": 14,
        "keywords": 7,
        "table_of_contents": 14,
        "introduction": 15,
        "background_review": 14,
        "objectives_and_route": 14,
        "materials": 15,
        "methods": 16,
        "results": 16,
        "discussion": 9,
        "chapter_summary": 11,
        "conclusion_and_outlook": 9,
        "references": 9,
        "acknowledgements": 5,
        "appendix": 3,
    },
    "heading_depth_counts": {"1": 401, "2": 350, "3": 645, "4": 298},
    "dominant_page": {
        "paper": "A4",
        "width_points": 595.3,
        "height_points": 841.9,
    },
    "docx_median_margins_inches": {
        "top": 1.08,
        "bottom": 0.98,
        "left": 0.98,
        "right": 0.98,
    },
    "observed_fonts": ["Times New Roman"],
    "observed_sizes_pt": [16, 12, 14, 22, 28, 11],
    "high_confidence_order": [
        "中文摘要与关键词",
        "英文摘要与关键词",
        "目录与图表/缩略词清单（按需）",
        "绪论：研究背景、文献综述、科学问题、研究目的与技术路线",
        "研究章节：引言 → 材料 → 方法 → 结果 → 讨论 → 本章小结",
        "结论与展望",
        "参考文献",
        "致谢与附录（按需）",
    ],
    "common_transitions": [
        ["材料", "方法", 30],
        ["章节引言", "材料", 28],
        ["方法", "结果", 28],
        ["结果", "讨论", 21],
        ["讨论", "本章小结", 21],
        ["文献综述", "研究目的与技术路线", 12],
        ["本章小结", "下一研究章引言", 13],
        ["结论与展望", "参考文献", 7],
    ],
    "writing_principles": [
        "目录层级通常深入到三级，复杂方法可使用四级标题。",
        "绪论负责收束背景、文献缺口、科学问题和技术路线，不提前混入结果。",
        "实证章节通常自成闭环：引言、材料、方法、结果、讨论、本章小结。",
        "结果与讨论必须语义分工：结果报告观察，讨论解释机制、局限和外推边界。",
        "结论只综合已论证内容，展望对应证据缺口和后续可验证问题。",
        "引用、图表和术语需在全文保持一致并可回溯到来源。",
    ],
}


def distilled_thesis_profile() -> dict[str, Any]:
    """Return a copy-safe structural profile without source thesis prose."""
    return {
        **DISTILLED_THESIS_PROFILE,
        "section_coverage": dict(DISTILLED_THESIS_PROFILE["section_coverage"]),
        "heading_depth_counts": dict(DISTILLED_THESIS_PROFILE["heading_depth_counts"]),
        "dominant_page": dict(DISTILLED_THESIS_PROFILE["dominant_page"]),
        "docx_median_margins_inches": dict(
            DISTILLED_THESIS_PROFILE["docx_median_margins_inches"]
        ),
        "observed_fonts": list(DISTILLED_THESIS_PROFILE["observed_fonts"]),
        "observed_sizes_pt": list(DISTILLED_THESIS_PROFILE["observed_sizes_pt"]),
        "high_confidence_order": list(
            DISTILLED_THESIS_PROFILE["high_confidence_order"]
        ),
        "common_transitions": [
            list(value) for value in DISTILLED_THESIS_PROFILE["common_transitions"]
        ],
        "writing_principles": list(DISTILLED_THESIS_PROFILE["writing_principles"]),
    }
