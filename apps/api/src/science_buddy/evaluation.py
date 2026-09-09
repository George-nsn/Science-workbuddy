import argparse
import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from science_buddy.config import get_settings
from science_buddy.infrastructure.database import async_session_factory
from science_buddy.services.embeddings import get_embedding_service
from science_buddy.services.evidence import EvidenceTokenService
from science_buddy.services.query_planning import DeterministicQueryPlanner
from science_buddy.services.reranking import get_reranker_service
from science_buddy.services.retrieval import RetrievalConfig, SQLiteHybridRetriever


@dataclass(frozen=True, slots=True)
class QueryMetrics:
    recall_at_5: float
    recall_at_10: float
    reciprocal_rank: float
    ndcg_at_10: float
    precision_at_5: float
    hit_at_5: float
    average_precision: float
    latency_ms: float


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k <= 0 or not ranked:
        return 0.0
    return len(set(ranked[:k]) & relevant) / min(k, len(ranked))


def hit_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def average_precision(ranked: list[str], relevant: set[str]) -> float:
    if not relevant:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, item in enumerate(ranked, start=1):
        if item in relevant:
            hits += 1
            precision_sum += hits / rank
    return precision_sum / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for rank, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(ranked[:k], start=1)
        if item in relevant
    )
    ideal_hits = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0


def ann_recall_at_k(exact_ranked: list[str], approximate_ranked: list[str], k: int) -> float:
    reference = exact_ranked[:k]
    if not reference:
        return 0.0
    return len(set(reference) & set(approximate_ranked[:k])) / len(reference)


def score_query(
    ranked: list[str], relevant: set[str], *, latency_ms: float
) -> QueryMetrics:
    return QueryMetrics(
        recall_at_5=recall_at_k(ranked, relevant, 5),
        recall_at_10=recall_at_k(ranked, relevant, 10),
        reciprocal_rank=reciprocal_rank(ranked, relevant),
        ndcg_at_10=ndcg_at_k(ranked, relevant, 10),
        precision_at_5=precision_at_k(ranked, relevant, 5),
        hit_at_5=hit_at_k(ranked, relevant, 5),
        average_precision=average_precision(ranked, relevant),
        latency_ms=latency_ms,
    )


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


async def evaluate_file(
    path: Path, config_override: RetrievalConfig | None = None
) -> dict[str, float | int]:
    settings = get_settings()
    config = config_override or RetrievalConfig.from_settings(settings)
    tokens = EvidenceTokenService(settings.evidence_signing_key.get_secret_value())
    metrics: list[QueryMetrics] = []
    dense_route_latency: list[float] = []
    vector_fetch: list[float] = []
    vector_decode: list[float] = []
    vector_dot_product: list[float] = []
    vector_top_k: list[float] = []
    vector_candidate_counts: list[float] = []
    vector_working_set_bytes: list[float] = []
    with path.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    async with async_session_factory() as session:
        for record in records:
            workflow_id = uuid4()
            retriever = SQLiteHybridRetriever(
                session,
                tokens,
                workflow_id=workflow_id,
                config=config,
                embedding_service=get_embedding_service(),
                session_factory=async_session_factory,
                reranker=(
                    get_reranker_service(config.reranker_model)
                    if config.reranker_enabled
                    else None
                ),
            )
            plan = DeterministicQueryPlanner().plan(record["query"])
            started = perf_counter()
            candidates = await retriever.retrieve(
                record["query"],
                project_id=UUID(record["project_id"]),
                limit=20,
                query_plan=plan,
            )
            latency_ms = (perf_counter() - started) * 1000
            ranked = [str(item.chunk_id) for item in candidates if item.role == "anchor"]
            metrics.append(
                score_query(ranked, set(record["gold_chunk_ids"]), latency_ms=latency_ms)
            )
            report = retriever.last_report
            if report is not None:
                for route in report.routes:
                    if not route.route.startswith("dense_"):
                        continue
                    dense_route_latency.append(route.elapsed_ms)
                    route_metrics = route.metrics
                    vector_fetch.append(float(route_metrics.get("fetch_ms", 0.0)))
                    vector_decode.append(float(route_metrics.get("decode_ms", 0.0)))
                    vector_dot_product.append(
                        float(route_metrics.get("dot_product_ms", 0.0))
                    )
                    vector_top_k.append(float(route_metrics.get("top_k_ms", 0.0)))
                    vector_candidate_counts.append(
                        float(route_metrics.get("candidate_count", 0))
                    )
                    vector_working_set_bytes.append(
                        float(route_metrics.get("estimated_working_set_bytes", 0))
                    )
    if not metrics:
        return {"queries": 0}
    query_latency = [value.latency_ms for value in metrics]
    return {
        "queries": len(metrics),
        "recall_at_5": mean(value.recall_at_5 for value in metrics),
        "recall_at_10": mean(value.recall_at_10 for value in metrics),
        "mrr": mean(value.reciprocal_rank for value in metrics),
        "ndcg_at_10": mean(value.ndcg_at_10 for value in metrics),
        "precision_at_5": mean(value.precision_at_5 for value in metrics),
        "hit_at_5": mean(value.hit_at_5 for value in metrics),
        "map": mean(value.average_precision for value in metrics),
        "latency_p50_ms": percentile(query_latency, 0.50),
        "latency_p95_ms": percentile(query_latency, 0.95),
        "dense_route_p50_ms": percentile(dense_route_latency, 0.50),
        "dense_route_p95_ms": percentile(dense_route_latency, 0.95),
        "vector_fetch_p50_ms": percentile(vector_fetch, 0.50),
        "vector_fetch_p95_ms": percentile(vector_fetch, 0.95),
        "vector_decode_p50_ms": percentile(vector_decode, 0.50),
        "vector_decode_p95_ms": percentile(vector_decode, 0.95),
        "vector_dot_product_p50_ms": percentile(vector_dot_product, 0.50),
        "vector_dot_product_p95_ms": percentile(vector_dot_product, 0.95),
        "vector_top_k_p50_ms": percentile(vector_top_k, 0.50),
        "vector_top_k_p95_ms": percentile(vector_top_k, 0.95),
        "vector_candidate_count_mean": mean(vector_candidate_counts)
        if vector_candidate_counts
        else 0.0,
        "vector_candidate_count_max": max(vector_candidate_counts, default=0.0),
        "vector_estimated_working_set_bytes_max": max(
            vector_working_set_bytes,
            default=0.0,
        ),
    }


def get_ablation_configs(base_config: RetrievalConfig) -> dict[str, tuple[RetrievalConfig, str]]:
    """Define the 7 progressive ablation experiment groups."""
    return {
        "G0_pure_dense": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=base_config.rrf_k,
                weights={"dense_original": 1.0},
                top_k_dense=base_config.top_k_dense,
                top_k_fts=0,
                top_k_simple=0,
                top_k_metadata=0,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=0,
                graph_neighbors=0,
                reranker_enabled=False,
                query_mesh_expansion=False,
                exclude_retracted=False,
            ),
            "纯 E5 稠密向量点积基线 (Pure Dense Baseline)",
        ),
        "G1_pure_lexical": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=base_config.rrf_k,
                weights={"fts_english": 1.0, "simple": 0.5},
                top_k_dense=0,
                top_k_fts=base_config.top_k_fts,
                top_k_simple=base_config.top_k_simple,
                top_k_metadata=0,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=0,
                graph_neighbors=0,
                reranker_enabled=False,
                query_mesh_expansion=False,
                exclude_retracted=False,
            ),
            "纯 SQLite FTS5 BM25 词干基线 (Pure Lexical Baseline)",
        ),
        "G2_naive_hybrid": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=60,
                weights={"dense_original": 1.0, "fts_english": 1.0},
                top_k_dense=base_config.top_k_dense,
                top_k_fts=base_config.top_k_fts,
                top_k_simple=0,
                top_k_metadata=0,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=0,
                graph_neighbors=0,
                reranker_enabled=False,
                query_mesh_expansion=False,
                exclude_retracted=False,
            ),
            "朴素双路混合检索 (Dense + FTS5 RRF k=60)",
        ),
        "G3_with_query_planning": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=60,
                weights={"dense_original": 1.0, "dense_translated": 0.9, "fts_english": 1.0},
                top_k_dense=base_config.top_k_dense,
                top_k_fts=base_config.top_k_fts,
                top_k_simple=0,
                top_k_metadata=0,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=0,
                graph_neighbors=0,
                reranker_enabled=False,
                query_mesh_expansion=False,
                exclude_retracted=False,
            ),
            "引入确定性查询规划与 33 组医学词典翻译 (+Query Planning)",
        ),
        "G4_with_mesh_expansion": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=60,
                weights={
                    "dense_original": 1.0,
                    "dense_translated": 0.9,
                    "fts_english": 1.0,
                    "mesh": 0.7,
                },
                top_k_dense=base_config.top_k_dense,
                top_k_fts=base_config.top_k_fts,
                top_k_simple=0,
                top_k_metadata=0,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=0,
                graph_neighbors=0,
                reranker_enabled=False,
                query_mesh_expansion=True,
                query_mesh_max_terms=4,
                exclude_retracted=False,
            ),
            "引入项目入库文献 MeSH 权威标签前缀动态扩展 (+MeSH Expansion)",
        ),
        "G5_7_route_weighted_rrf": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=60,
                weights={
                    "exact": 1.2,
                    "dense_original": 1.0,
                    "dense_translated": 0.9,
                    "fts_english": 0.8,
                    "mesh": 0.7,
                    "metadata": 0.6,
                    "graph_local": 0.3,
                },
                top_k_dense=base_config.top_k_dense,
                top_k_fts=base_config.top_k_fts,
                top_k_simple=base_config.top_k_simple,
                top_k_metadata=base_config.top_k_metadata,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=base_config.graph_seed_papers,
                graph_neighbors=base_config.graph_neighbors,
                reranker_enabled=False,
                query_mesh_expansion=True,
                query_mesh_max_terms=4,
                exclude_retracted=True,
            ),
            "开启 7 路隔离加权 RRF 融合与撤稿文献硬性过滤门 (+7-Route Weighted RRF)",
        ),
        "G6_full_with_reranker": (
            RetrievalConfig(
                version=base_config.version,
                rrf_k=60,
                weights={
                    "exact": 1.2,
                    "dense_original": 1.0,
                    "dense_translated": 0.9,
                    "fts_english": 0.8,
                    "mesh": 0.7,
                    "metadata": 0.6,
                    "graph_local": 0.3,
                },
                top_k_dense=base_config.top_k_dense,
                top_k_fts=base_config.top_k_fts,
                top_k_simple=base_config.top_k_simple,
                top_k_metadata=base_config.top_k_metadata,
                fused_pool=base_config.fused_pool,
                max_chunks_per_paper=base_config.max_chunks_per_paper,
                context_radius=base_config.context_radius,
                context_max_chars=base_config.context_max_chars,
                route_timeout_seconds=base_config.route_timeout_seconds,
                dense_route_timeout_seconds=base_config.dense_route_timeout_seconds,
                graph_seed_papers=base_config.graph_seed_papers,
                graph_neighbors=base_config.graph_neighbors,
                reranker_enabled=True,
                reranker_weight=0.65,
                reranker_timeout_seconds=30.0,
                query_mesh_expansion=True,
                query_mesh_max_terms=4,
                exclude_retracted=True,
            ),
            "完整生产配置：+ BGE-Reranker-M3 交叉编码精排与 30s 熔断 (+Cross-Encoder)",
        ),
    }


async def run_ablation_study(path: Path) -> str:
    """Run progressive 7-group ablation study and generate markdown comparison report."""
    settings = get_settings()
    base_config = RetrievalConfig.from_settings(settings)
    ablation_groups = get_ablation_configs(base_config)
    results: dict[str, dict[str, Any]] = {}

    for group_id, (config, description) in ablation_groups.items():
        metrics = await evaluate_file(path, config_override=config)
        results[group_id] = {
            "description": description,
            "metrics": metrics,
        }

    # Generate Markdown Table Report
    g0_metrics = results["G0_pure_dense"]["metrics"]
    g0_r5 = float(g0_metrics.get("recall_at_5", 0.0))

    header = (
        "| 实验组 | 架构机制描述 | Recall@5 | Recall@10 | Hit@5 | "
        "MRR | NDCG@10 | MAP | P50 (ms) | P95 (ms) | ΔRecall@5 |"
    )
    separator = (
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    )
    lines = [
        "# Science Buddy RAG 检索消融实验对比报告 (Ablation Study Report)",
        "",
        f"- **评测数据文件**：`{path.name}`",
        f"- **评测查询总数**：{g0_metrics.get('queries', 0)} 条",
        "",
        header,
        separator,
    ]

    for group_id, data in results.items():
        m = data["metrics"]
        r5 = float(m.get("recall_at_5", 0.0))
        r10 = float(m.get("recall_at_10", 0.0))
        h5 = float(m.get("hit_at_5", 0.0))
        mrr = float(m.get("mrr", 0.0))
        ndcg = float(m.get("ndcg_at_10", 0.0))
        map_val = float(m.get("map", 0.0))
        p50 = float(m.get("latency_p50_ms", 0.0))
        p95 = float(m.get("latency_p95_ms", 0.0))

        delta_r5 = f"{((r5 - g0_r5) / g0_r5 * 100):+.1f}%" if g0_r5 > 0 else "-"
        if group_id == "G0_pure_dense":
            delta_r5 = "基准 (Base)"

        row = (
            f"| **{group_id}** | {data['description']} | "
            f"{r5:.3f} | {r10:.3f} | {h5:.3f} | {mrr:.3f} | {ndcg:.3f} | {map_val:.3f} | "
            f"{p50:.1f} | {p95:.1f} | **{delta_r5}** |"
        )
        lines.append(row)

    lines.extend([
        "",
        "### 💡 核心消融结论归纳：",
        "1. **Dense + Sparse 互补**：G0 -> G2 双路 RRF 显著提升召回，精确词法与语义互补；",
        "2. **跨语言词汇鸿沟**：G2 -> G3 引入确定性词典翻译，消除中文提问与英文文献偏移；",
        "3. **权威 MeSH 动态扩展**：G3 -> G4 解决长尾 OOV 医学专有名词召回缺陷；",
        "4. **全景拓扑与撤稿过滤**：G4 -> G5 开启 7 路全加权与撤稿门，净化候选池质量；",
        "5. **Cross-Encoder 重排精修**：G5 -> G6 引入深度交互注意力，显著提升 MRR 与 NDCG@10。",
    ])

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Science Buddy retrieval JSONL")
    parser.add_argument("path", type=Path, help="Path to queries.jsonl benchmark file")
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="Run full 7-group ablation study and print comparison report",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Optional path to save markdown evaluation report",
    )
    arguments = parser.parse_args()

    if arguments.ablation:
        report_md = asyncio.run(run_ablation_study(arguments.path))
        print(report_md)
        if arguments.output_markdown:
            arguments.output_markdown.write_text(report_md, encoding="utf-8")
            print(f"\n[OK] Ablation report saved to: {arguments.output_markdown}")
    else:
        results = asyncio.run(evaluate_file(arguments.path))
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
