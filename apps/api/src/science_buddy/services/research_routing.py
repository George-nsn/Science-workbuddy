import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from science_buddy.domain.contracts import RetrievalCandidate

QuestionType = Literal[
    "identifier",
    "comparison",
    "mechanism",
    "pico",
    "methodology",
    "systematic_review",
    "latest_progress",
    "general",
]
ResearchStrategy = Literal["direct", "decomposition", "deep_research"]
StrategyPreference = Literal["auto", "direct", "decomposition", "deep_research"]

_CLAUSE_SPLIT = re.compile(r"[;；。!?！？]\s*|\s+(?:and|versus|vs\.?|以及|并且)\s+", re.I)
_COMPARISON = re.compile(
    r"\b(?:versus|vs\.?|compared with|comparison|difference between)\b|"
    r"比较|对比|优于|劣于|差异|哪种更",
    re.I,
)
_MECHANISM = re.compile(
    r"\b(?:mechanism|pathway|mediate[sd]?|causal|cause|why|how does)\b|"
    r"机制|通路|因果|导致|为何|为什么|如何作用",
    re.I,
)
_PICO = re.compile(
    r"\b(?:patients?|participants?|population|intervention|comparator|outcome|"
    r"clinical|randomi[sz]ed|placebo|treatment)\b|"
    r"患者|人群|干预|对照|结局|临床|随机|安慰剂|疗效|不良反应",
    re.I,
)
_METHOD = re.compile(
    r"\b(?:method(?:ology)?|protocol|assay|workflow|benchmark|evaluation metric|"
    r"sample size|statistical method)\b|"
    r"方法学|实验方法|方案|流程|检测方法|评价指标|样本量|统计方法|质控",
    re.I,
)
_SYSTEMATIC = re.compile(
    r"\b(?:systematic review|meta[- ]?analysis|evidence synthesis|prisma|"
    r"all studies|comprehensive review)\b|"
    r"系统综述|荟萃分析|Meta分析|证据综合|PRISMA|所有研究|全面综述",
    re.I,
)
_LATEST = re.compile(
    r"\b(?:latest|recent|current state|state of the art|emerging|new advances?|"
    r"past (?:three|five|ten|\d+) years?)\b|"
    r"最新|近期|当前进展|研究前沿|新进展|近[三五十\d]+年|截至目前",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ResearchSubquery:
    subquery_id: str
    focus: str
    query: str


@dataclass(frozen=True, slots=True)
class ResearchRoutingDecision:
    question_type: QuestionType
    strategy: ResearchStrategy
    confidence: float
    reason_codes: tuple[str, ...]
    subqueries: tuple[ResearchSubquery, ...]
    max_followup_rounds: int
    version: str = "research-routing-v1"


@dataclass(frozen=True, slots=True)
class ControlledRetrievalStep:
    subquery_id: str
    focus: str
    query: str
    round: int
    candidates: tuple[RetrievalCandidate, ...]
    cache_level: str
    route_errors: tuple[str, ...] = ()
    report: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ControlledRetrievalResult:
    decision: ResearchRoutingDecision
    candidates: tuple[RetrievalCandidate, ...]
    steps: tuple[ControlledRetrievalStep, ...]
    followup_queries: tuple[str, ...]


ResearchRetriever = Callable[
    [str, str, str, int], Awaitable[ControlledRetrievalStep]
]


class DeterministicResearchRouter:
    """Classify and decompose research questions without granting tools to an LLM."""

    def route(
        self,
        question: str,
        *,
        preference: StrategyPreference = "auto",
        max_subqueries: int = 4,
        max_followup_rounds: int = 1,
        identifier: bool = False,
    ) -> ResearchRoutingDecision:
        normalized = " ".join(question.strip().split())
        if not normalized:
            raise ValueError("research question must not be blank")
        if identifier:
            question_type: QuestionType = "identifier"
            confidence = 1.0
            reasons = ["exact_identifier"]
        else:
            question_type, confidence, reasons = self._classify(normalized)
        strategy = self._strategy(question_type, normalized, preference)
        subqueries = self._decompose(
            normalized,
            question_type=question_type,
            strategy=strategy,
            max_subqueries=max_subqueries,
        )
        return ResearchRoutingDecision(
            question_type=question_type,
            strategy=strategy,
            confidence=confidence,
            reason_codes=tuple(reasons),
            subqueries=tuple(subqueries),
            max_followup_rounds=(
                min(max(max_followup_rounds, 0), 1)
                if strategy == "deep_research"
                else 0
            ),
        )

    @staticmethod
    def _classify(question: str) -> tuple[QuestionType, float, list[str]]:
        matches: list[tuple[QuestionType, str]] = []
        marker_rules: tuple[tuple[QuestionType, str, re.Pattern[str]], ...] = (
            ("systematic_review", "systematic_review_marker", _SYSTEMATIC),
            ("latest_progress", "recency_marker", _LATEST),
            ("pico", "clinical_pico_marker", _PICO),
            ("comparison", "comparison_marker", _COMPARISON),
            ("mechanism", "mechanism_marker", _MECHANISM),
            ("methodology", "methodology_marker", _METHOD),
        )
        for question_type, code, pattern in marker_rules:
            if pattern.search(question):
                matches.append((question_type, code))
        if not matches:
            return "general", 0.55, ["no_specialized_marker"]
        priority = {
            "systematic_review": 6,
            "latest_progress": 5,
            "pico": 4,
            "comparison": 3,
            "mechanism": 2,
            "methodology": 1,
        }
        question_type = max(matches, key=lambda item: priority[item[0]])[0]
        reasons = [code for _, code in matches]
        confidence = min(0.97, 0.72 + 0.08 * len(matches))
        return question_type, confidence, reasons

    @staticmethod
    def _strategy(
        question_type: QuestionType,
        question: str,
        preference: StrategyPreference,
    ) -> ResearchStrategy:
        if question_type == "identifier":
            return "direct"
        if preference != "auto":
            return preference
        if question_type in {"systematic_review", "latest_progress"}:
            return "deep_research"
        if question_type in {"comparison", "pico"}:
            return "decomposition"
        if question_type == "mechanism" and (
            _COMPARISON.search(question) or len(question) > 180
        ):
            return "decomposition"
        return "direct"

    def _decompose(
        self,
        question: str,
        *,
        question_type: QuestionType,
        strategy: ResearchStrategy,
        max_subqueries: int,
    ) -> list[ResearchSubquery]:
        if strategy == "direct" or max_subqueries <= 1:
            return [ResearchSubquery("q1", "original", question)]
        focus_queries: list[tuple[str, str]] = [("core", question)]
        if question_type == "comparison":
            focus_queries.extend(
                [
                    ("comparative_effectiveness", f"{question} comparative effectiveness"),
                    ("comparative_safety", f"{question} safety adverse events"),
                ]
            )
        elif question_type == "pico":
            focus_queries.extend(
                [
                    ("population_intervention", f"{question} population intervention"),
                    ("comparator_outcomes", f"{question} comparator outcomes"),
                    ("study_design", f"{question} randomized controlled trial"),
                ]
            )
        elif question_type == "systematic_review":
            focus_queries.extend(
                [
                    ("primary_studies", f"{question} primary study"),
                    ("systematic_reviews", f"{question} systematic review meta analysis"),
                    ("citation_landscape", f"{question} references cited by citation network"),
                    ("bias_heterogeneity", f"{question} risk of bias heterogeneity"),
                ]
            )
        elif question_type == "latest_progress":
            focus_queries.extend(
                [
                    ("recent_primary", f"{question} recent primary research"),
                    ("recent_reviews", f"{question} recent review"),
                    ("citation_landscape", f"{question} citation network emerging papers"),
                    ("open_questions", f"{question} limitations research gaps"),
                ]
            )
        elif question_type == "mechanism":
            focus_queries.extend(
                [
                    ("pathway", f"{question} molecular pathway"),
                    ("causal_evidence", f"{question} causal evidence perturbation"),
                    ("alternative_mechanisms", f"{question} alternative mechanism"),
                ]
            )
        elif question_type == "methodology":
            focus_queries.extend(
                [
                    ("validation", f"{question} validation benchmark"),
                    ("bias_reproducibility", f"{question} bias reproducibility quality control"),
                ]
            )
        clauses = [value.strip(" ,，") for value in _CLAUSE_SPLIT.split(question)]
        if len(clauses) > 1:
            focus_queries.extend(("clause", clause) for clause in clauses if len(clause) > 8)
        deduplicated: list[ResearchSubquery] = []
        seen: set[str] = set()
        for focus, query in focus_queries:
            normalized = " ".join(query.split())
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(
                ResearchSubquery(
                    subquery_id=f"q{len(deduplicated) + 1}",
                    focus=focus,
                    query=normalized,
                )
            )
            if len(deduplicated) >= max(1, min(max_subqueries, 6)):
                break
        return deduplicated

    @staticmethod
    def followup_queries(
        question: str,
        *,
        gaps: Sequence[str],
        existing_queries: Sequence[str],
        limit: int = 2,
    ) -> tuple[str, ...]:
        existing = {" ".join(value.split()).casefold() for value in existing_queries}
        output: list[str] = []
        for gap in gaps:
            cleaned = " ".join(str(gap).strip().split())
            if len(cleaned) < 3:
                continue
            query = f"{question} evidence gap {cleaned[:240]}"
            key = query.casefold()
            if key in existing:
                continue
            existing.add(key)
            output.append(query)
            if len(output) >= max(0, min(limit, 2)):
                break
        return tuple(output)


class ControlledResearchRetriever:
    """Run bounded parallel retrieval; agents never receive a free-form tool handle."""

    def __init__(self, retrieve: ResearchRetriever, *, max_parallel: int = 2) -> None:
        self._retrieve = retrieve
        self._max_parallel = max(1, min(max_parallel, 4))

    async def execute(
        self,
        decision: ResearchRoutingDecision,
        *,
        limit: int,
        followup_queries: Sequence[str] = (),
        include_initial: bool = True,
    ) -> ControlledRetrievalResult:
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def run(
            query: str,
            subquery_id: str,
            focus: str,
            round_number: int,
        ) -> ControlledRetrievalStep:
            async with semaphore:
                return await self._retrieve(query, subquery_id, focus, round_number)

        first_steps: list[ControlledRetrievalStep] = []
        if include_initial:
            first_steps = list(
                await asyncio.gather(
                    *(
                        run(
                            subquery.query,
                            subquery.subquery_id,
                            subquery.focus,
                            0,
                        )
                        for subquery in decision.subqueries
                    )
                )
            )
        followups = tuple(followup_queries[:2])
        followup_steps: list[ControlledRetrievalStep] = []
        if decision.max_followup_rounds > 0 and followups:
            followup_steps = list(
                await asyncio.gather(
                    *(
                        run(query, f"followup-{index}", "evidence_gap", 1)
                        for index, query in enumerate(followups, start=1)
                    )
                )
            )
        steps = [*first_steps, *followup_steps]
        return ControlledRetrievalResult(
            decision=decision,
            candidates=tuple(self.merge_candidates(steps, limit=limit)),
            steps=tuple(steps),
            followup_queries=followups,
        )

    @staticmethod
    def merge_candidates(
        steps: Sequence[ControlledRetrievalStep],
        *,
        limit: int,
    ) -> list[RetrievalCandidate]:
        by_chunk: dict[UUID, RetrievalCandidate] = {}
        hit_counts: dict[UUID, int] = {}
        for step in steps:
            for candidate in step.candidates:
                current = by_chunk.get(candidate.chunk_id)
                hit_counts[candidate.chunk_id] = hit_counts.get(candidate.chunk_id, 0) + 1
                if current is None or candidate.score > current.score:
                    by_chunk[candidate.chunk_id] = candidate
        ranked = sorted(
            by_chunk.values(),
            key=lambda item: (hit_counts[item.chunk_id], item.score),
            reverse=True,
        )
        anchors = [candidate for candidate in ranked if candidate.role == "anchor"]
        anchor_ids = {candidate.chunk_id for candidate in anchors[:limit]}
        selected = [
            candidate
            for candidate in ranked
            if candidate.role == "anchor" or candidate.anchor_chunk_id in anchor_ids
        ]
        return selected[: max(limit, 1) * 3]


def routing_audit_payload(decision: ResearchRoutingDecision) -> dict[str, object]:
    return {
        "question_type": decision.question_type,
        "strategy": decision.strategy,
        "confidence": decision.confidence,
        "reason_codes": list(decision.reason_codes),
        "max_followup_rounds": decision.max_followup_rounds,
        "version": decision.version,
        "subqueries": [
            {
                "subquery_id": item.subquery_id,
                "focus": item.focus,
                "query": item.query,
            }
            for item in decision.subqueries
        ],
    }


def retrieval_steps_payload(
    steps: Sequence[ControlledRetrievalStep],
) -> list[dict[str, object]]:
    return [
        {
            "subquery_id": step.subquery_id,
            "focus": step.focus,
            "query": step.query,
            "round": step.round,
            "candidates": len(step.candidates),
            "cache_level": step.cache_level,
            "route_errors": list(step.route_errors),
        }
        for step in steps
    ]
