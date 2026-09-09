"use client";

import { CheckCircle2, Database, Download, FileJson, FileSearch, LoaderCircle, Search, ShieldCheck, Sparkles, XCircle } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import { API_URL, apiRequest, type LibraryResponse, type RagCollection, type ResearchResponse, type ResearchReview, type ResearchRouting, type ResearchTrace, type RetrievalStep } from "@/lib/api";

type RetrievalResponse = {
  workflow_id: string;
  retrieval_mode: "identifier" | "hybrid-sparse" | "hybrid-dense";
  retrieval_version: string;
  cache_level: "miss" | "l1-memory" | "l2-redis" | "l3-sqlite";
  elapsed_ms: number;
  reranker: {
    enabled: boolean;
    applied: boolean;
    model: string | null;
    candidates: number;
    elapsed_ms: number;
    error: string | null;
  };
  excluded_retracted: number;
  routing: ResearchRouting;
  retrieval_steps: RetrievalStep[];
  query_plan: {
    language: "zh" | "en" | "mixed";
    original_query: string;
    english_query: string | null;
    expansions: Array<{ source: string; target: string }>;
  };
  routes: Array<{
    route: string;
    candidates: number;
    elapsed_ms: number;
    error: string | null;
  }>;
  items: Array<{
    chunk_id: string;
    evidence_id: string;
    text: string;
    score: number;
    source_locator: Record<string, unknown>;
    paper_id: string | null;
    role: "anchor" | "neighbor";
    anchor_chunk_id: string | null;
    traces: Array<{
      route: string;
      rank: number;
      raw_score: number;
      weighted_rrf: number;
    }>;
  }>;
};

export function EvidenceWorkbench() {
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [collectionId, setCollectionId] = useState("");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<RetrievalResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [modelConfigured, setModelConfigured] = useState(false);
  const [research, setResearch] = useState<ResearchResponse | null>(null);
  const [researchTrace, setResearchTrace] = useState<ResearchTrace | null>(null);
  const [researchReview, setResearchReview] = useState<ResearchReview | null>(null);
  const [publishingFacts, setPublishingFacts] = useState(false);
  const [factsPublished, setFactsPublished] = useState(false);
  const [strategy, setStrategy] = useState<"auto" | "direct" | "decomposition" | "deep_research">("auto");
  const [allowPubmedSearch, setAllowPubmedSearch] = useState(false);
  const [allowWebSearch, setAllowWebSearch] = useState(false);
  const [allowAutoImport, setAllowAutoImport] = useState(false);
  const [dynamicPlanning, setDynamicPlanning] = useState(true);
  const [modelDepth, setModelDepth] = useState<"quick" | "balanced" | "deep" | "max">("balanced");
  const [maxContextTokens, setMaxContextTokens] = useState<32768 | 65536 | 131072 | 262144 | 524288 | 1000000>(65536);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    apiRequest<LibraryResponse>("/library", { cache: "no-store" })
      .then((value) => {
        if (active) {
          setLibrary(value);
          void apiRequest<{ collections: RagCollection[] }>(
            `/rag/collections?project_id=${value.project_id}`,
            { cache: "no-store" },
          ).then((collectionValue) => {
            if (active) setCollections(collectionValue.collections);
          });
        }
      })
      .catch((value: unknown) => {
        if (active) {
          setError(value instanceof Error ? value.message : "文献库不可用");
        }
      });
    apiRequest<{ configured: boolean }>("/research/model-status", { cache: "no-store" })
      .then((value) => { if (active) setModelConfigured(value.configured); })
      .catch(() => { if (active) setModelConfigured(false); });
    return () => {
      active = false;
    };
  }, []);

  async function retrieve(event: FormEvent) {
    event.preventDefault();
    if (!library || query.trim().length < 2) return;
    setLoading(true);
    setError(null);
    try {
      setResult(
        await apiRequest<RetrievalResponse>("/retrieval/search", {
          method: "POST",
          body: JSON.stringify({
            project_id: library.project_id,
            collection_id: collectionId || null,
            query: query.trim(),
            limit: 12,
            strategy,
            max_subqueries: 4,
            max_followup_rounds: 1,
          }),
        }),
      );
    } catch (value) {
      setError(value instanceof Error ? value.message : "证据检索失败");
    } finally {
      setLoading(false);
    }
  }

  async function generateAnswer() {
    if (!library || query.trim().length < 2) return;
    setGenerating(true);
    setError(null);
    try {
      const response = await apiRequest<ResearchResponse>("/research/answer", {
        method: "POST",
        body: JSON.stringify({
          project_id: library.project_id,
          collection_id: collectionId || null,
          question: query.trim(),
          language: "zh-CN",
          retrieval_limit: 12,
          strategy,
          max_subqueries: 4,
          max_followup_rounds: 1,
          allow_pubmed_search: allowPubmedSearch,
          allow_web_search: allowWebSearch,
          allow_auto_import: allowAutoImport,
          dynamic_planning: dynamicPlanning,
          max_external_requests: modelDepth === "quick" ? 1 : modelDepth === "balanced" ? 2 : 4,
          model_depth: modelDepth,
          max_context_tokens: maxContextTokens,
        }),
      });
      setResearch(response);
      const [trace, review] = await Promise.all([
        apiRequest<ResearchTrace>(`/research/${response.run_id}/trace`, { cache: "no-store" }),
        apiRequest<ResearchReview>(`/research/${response.run_id}/review`, { cache: "no-store" }),
      ]);
      setResearchTrace(trace);
      setResearchReview(review);
      setFactsPublished(false);
    } catch (value) {
      setError(value instanceof Error ? value.message : "可信回答生成失败");
    } finally {
      setGenerating(false);
    }
  }

  async function publishFacts() {
    if (!research || publishingFacts || factsPublished) return;
    setPublishingFacts(true);
    setError(null);
    try {
      await apiRequest(`/research/${research.run_id}/publish-facts`, {
        method: "POST",
        body: JSON.stringify({ confirmed: true, published_by: "user" }),
      });
      setFactsPublished(true);
    } catch (value) {
      setError(value instanceof Error ? value.message : "写入长期事实失败");
    } finally {
      setPublishingFacts(false);
    }
  }

  return (
    <>
      <header className="page-header">
        <div>
          <span className="eyebrow">TRACEABLE EVIDENCE RETRIEVAL</span>
          <h1>证据检索</h1>
          <p>返回原始文献块与防篡改 Evidence ID。当前不生成未经验证的科研结论。</p>
        </div>
        <div className="header-actions">
          <div className={`model-badge ${modelConfigured ? "configured" : ""}`}>
            <span /> {modelConfigured ? "模型已配置" : "仅本地检索"}
          </div>
          <div className="boundary-badge"><ShieldCheck size={16} /> 候选集外引用将被拒绝</div>
        </div>
      </header>
      <section className="search-panel evidence-search">
        <form onSubmit={retrieve}>
          <label className="collection-scope-select">
            检索范围
            <select onChange={(event) => setCollectionId(event.target.value)} value={collectionId}>
              <option value="">整个项目文献库</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.name}（{collection.paper_count} 篇）
                </option>
              ))}
            </select>
          </label>
          <label className="collection-scope-select">
            模型思考深度
            <select onChange={(event) => setModelDepth(event.target.value as typeof modelDepth)} value={modelDepth}>
              <option value="quick">快速</option>
              <option value="balanced">均衡</option>
              <option value="deep">Deep</option>
              <option value="max">Max</option>
            </select>
          </label>
          <label className="collection-scope-select">
            最大上下文
            <select onChange={(event) => setMaxContextTokens(Number(event.target.value) as typeof maxContextTokens)} value={maxContextTokens}>
              <option value={32768}>32K</option>
              <option value={65536}>64K</option>
              <option value={131072}>128K</option>
              <option value={262144}>256K</option>
              <option value={524288}>512K</option>
              <option value={1000000}>1M</option>
            </select>
          </label>
          <label className="collection-scope-select">
            研究路由
            <select onChange={(event) => setStrategy(event.target.value as typeof strategy)} value={strategy}>
              <option value="auto">自动判断复杂度</option>
              <option value="direct">Direct Search</option>
              <option value="decomposition">Decomposition</option>
              <option value="deep_research">Deep Research</option>
            </select>
          </label>
          <label className="collection-scope-select">
            <span>受控动态研究</span>
            <span><input checked={dynamicPlanning} onChange={(event) => setDynamicPlanning(event.target.checked)} type="checkbox" /> 允许模型提出探索动作（后端白名单执行）</span>
          </label>
          <label className="collection-scope-select">
            <span>学术来源</span>
            <span><input checked={allowPubmedSearch} onChange={(event) => setAllowPubmedSearch(event.target.checked)} type="checkbox" /> 允许四来源学术发现</span>
          </label>
          <label className="collection-scope-select">
            <span>自动入库</span>
            <span><input checked={allowAutoImport} onChange={(event) => setAllowAutoImport(event.target.checked)} type="checkbox" /> 允许已发现论文写入当前项目</span>
          </label>
          <label className="collection-scope-select">
            <span>网页上下文</span>
            <span><input checked={allowWebSearch} onChange={(event) => setAllowWebSearch(event.target.checked)} type="checkbox" /> 允许受控 Tavily（不作为科研证据）</span>
          </label>
          <div className="search-box">
            <Search size={20} />
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入科研问题、PMID、PMCID 或 DOI"
              value={query}
            />
            <button disabled={loading || !library || query.trim().length < 2} type="submit">
              {loading ? <LoaderCircle className="spin" size={17} /> : <FileSearch size={17} />}
              检索证据
            </button>
          </div>
        </form>
      </section>
      {error && <div className="error-banner">{error}</div>}
      {result && (
        <section className="results-section">
          <div className="results-heading">
            <div><span className="eyebrow">EVIDENCE CANDIDATES</span><h2>原文证据</h2></div>
            <span>{result.retrieval_mode} · {result.cache_level} · {Math.round(result.elapsed_ms)} ms</span>
          </div>
          <div className="query-plan-card">
            <div><strong>查询计划</strong><span>{result.query_plan.language} · {result.retrieval_version}</span></div>
            <p><b>研究问题类型</b>{questionTypeLabel(result.routing.question_type)} · {strategyLabel(result.routing.strategy)} · 置信度 {Math.round(result.routing.confidence * 100)}%</p>
            <p><b>语义召回</b>{result.routes.some((route) => route.route.includes("dense_") && route.candidates > 0) ? "本地 E5 已命中" : "本轮未命中向量候选"} · <b>CrossEncoder</b>{result.reranker.applied ? `${result.reranker.model ?? "本地模型"} 已重排 ${result.reranker.candidates} 条` : result.reranker.enabled ? `已启用但未应用${result.reranker.error ? `：${result.reranker.error}` : ""}` : "未启用"}</p>
            <div className="route-summary">
              {result.routing.subqueries.map((item) => (
                <span key={item.subquery_id}>{item.subquery_id} · {item.focus} · {item.query}</span>
              ))}
            </div>
            {result.query_plan.english_query && result.query_plan.english_query !== result.query_plan.original_query && (
              <p><b>英文检索式</b>{result.query_plan.english_query}</p>
            )}
            <div className="route-summary">
              {result.routes.map((route) => (
                <span className={route.error ? "route-error" : ""} key={route.route}>
                  {route.route} · {route.candidates} · {Math.round(route.elapsed_ms)}ms
                </span>
              ))}
            </div>
          </div>
          <div className="evidence-list">
            {result.items.map((item, index) => (
              <article className="evidence-card" key={item.evidence_id}>
                <header>
                  <span>EV {String(index + 1).padStart(2, "0")} · {item.role}</span>
                  <code>{item.evidence_id}</code>
                </header>
                <p>{item.text}</p>
                <footer>
                  <span>{item.traces.map((trace) => trace.route).join(" + ") || "context"}</span>
                  <span>{String(item.source_locator.section_path ?? "未知章节")}</span>
                </footer>
              </article>
            ))}
          </div>
          <div className="answer-action">
            <p>{modelConfigured ? "将执行声明抽取、机械校验、语义核验和受控合成。" : "配置后端 LLM 环境变量后才可生成回答；密钥不会进入浏览器。"}</p>
            <button disabled={!modelConfigured || generating} onClick={generateAnswer}>
              {generating ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
              {generating ? "三阶段校验中" : "生成可信回答"}
            </button>
          </div>
        </section>
      )}
      {research && (
        <section className="research-answer">
          <header>
            <div><span className="eyebrow">VERIFIED RESEARCH OUTPUT</span><h2>校验后回答</h2></div>
            <div className="export-actions">
              <a href={`${API_URL}/research/${research.run_id}/export?format=markdown`}><Download size={14} /> Markdown</a>
              <a href={`${API_URL}/research/${research.run_id}/export?format=docx`}><Download size={14} /> DOCX</a>
              <a href={`${API_URL}/research/${research.run_id}/export?format=review`}><FileSearch size={14} /> 可追溯综述</a>
              <a href={`${API_URL}/research/${research.run_id}/audit.json`}><FileJson size={14} /> JSON 审计包</a>
              <button disabled={publishingFacts || factsPublished || !research.result.claims.length} onClick={publishFacts}>
                {publishingFacts ? <LoaderCircle className="spin" size={14} /> : <Database size={14} />}
                {factsPublished ? "已写入长期事实" : "确认结论并记忆"}
              </button>
            </div>
          </header>
          <p className="answer-text">{research.result.answer}</p>
          {research.routing && <div className="query-plan-card"><div><strong>受控研究路由</strong><span>{questionTypeLabel(research.routing.question_type)} · {strategyLabel(research.routing.strategy)}</span></div><div className="route-summary">{research.tool_actions.map((action, index) => <span className={action.error ? "route-error" : ""} key={`${action.tool}-${index}`}>{action.tool} · {action.executed ? "已执行" : "仅建议"} · {action.results} 条</span>)}</div></div>}
          {research.research_plan && (
            <section className="research-audit-panel">
              <header>
                <div><span className="eyebrow">CONTROLLED RESEARCH AUDIT</span><h3>计划、动作与充分性</h3></div>
                <span className={`audit-source ${research.research_plan.planner_error ? "fallback" : ""}`}>
                  {planSourceLabel(research.research_plan.source)}
                </span>
              </header>
              <div className="audit-summary-grid">
                <article><strong>{research.research_plan.max_rounds}</strong><span>最大探索轮次</span></article>
                <article><strong>{research.research_plan.max_external_requests}</strong><span>外部请求预算</span></article>
                <article><strong>{research.research_actions.filter((action) => action.approved).length}</strong><span>后端批准动作</span></article>
                <article><strong>{research.sufficiency.length}</strong><span>充分性判定</span></article>
              </div>
              <div className="audit-columns">
                <div>
                  <h4>确定性骨架与模型扩展</h4>
                  <div className="audit-query-groups">
                    <AuditQueryGroup label="必需覆盖" values={research.research_plan.required_subqueries} />
                    <AuditQueryGroup label="探索扩展" values={research.research_plan.exploratory_subqueries} />
                    <AuditQueryGroup label="反证检索" values={research.research_plan.counterevidence_subqueries} />
                  </div>
                  {research.research_plan.planner_error && <p className="audit-warning">规划模型降级：{research.research_plan.planner_error}；已使用确定性 fallback。</p>}
                </div>
                <div>
                  <h4>不可变进度</h4>
                  <ol className="research-progress-list">
                    {research.progress.map((step) => (
                      <li key={step.step_number}>
                        <span>{String(step.step_number).padStart(2, "0")}</span>
                        <div><strong>{stepTypeLabel(step.step_type)}</strong><small>第 {step.round_number} 轮 · {step.decision}</small></div>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
              {!!research.research_actions.length && (
                <div className="research-action-grid">
                  {research.research_actions.map((action, index) => (
                    <article className={action.approved ? "approved" : "rejected"} key={`${action.action_id}-${index}`}>
                      <header>{action.approved ? <CheckCircle2 size={14} /> : <XCircle size={14} />}<strong>R{action.round_number} · {actionTypeLabel(action.action_type)}</strong><span>{Math.round(action.decision_score * 100)} 分</span></header>
                      <p>{action.query || "停止研究"}</p>
                      <small>{action.approved ? action.rationale || "通过白名单、授权、预算与多样性校验" : rejectionLabel(action.rejection_reason)}</small>
                    </article>
                  ))}
                </div>
              )}
              {!!research.sufficiency.length && (
                <div className="sufficiency-strip">
                  {research.sufficiency.map((verdict) => (
                    <article className={verdict.sufficient ? "sufficient" : "bounded-stop"} key={`${verdict.stop_reason}-${verdict.round_number}`}>
                      <span>ROUND {verdict.round_number}</span><strong>{verdict.sufficient ? "证据充分" : "受预算约束停止"}</strong>
                      <div><i style={{ width: `${Math.round(verdict.answer_coverage * 100)}%` }} /></div>
                      <small>回答覆盖 {Math.round(verdict.answer_coverage * 100)}% · 反证覆盖 {Math.round(verdict.counterevidence_coverage * 100)}% · {stopReasonLabel(verdict.stop_reason)}</small>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}
          {researchTrace && (
            <section className="research-trace-detail">
              <header><div><span className="eyebrow">RESEARCH TRAJECTORY</span><h3>研究轨迹与 Claim—Evidence 矩阵</h3></div><span>{researchTrace.plan_versions.length} 个计划版本</span></header>
              <div className="plan-version-flow">
                {researchTrace.plan_versions.map((version) => (
                  <article key={version.version_number}>
                    <strong>V{version.version_number} · {planSourceLabel(version.source)}</strong>
                    <span>新增 Claim {version.added_claim_ids.length} · 新增动作 {version.added_action_ids.length} · 新增查询 {version.added_queries.length}</span>
                    {!!version.added_queries.length && <small>{version.added_queries.join("；")}</small>}
                  </article>
                ))}
              </div>
              <div className="round-budget-grid">
                {researchTrace.rounds.map((round) => (
                  <article key={round.round_number}>
                    <span>ROUND {round.round_number}</span><strong>批准 {round.approved_actions} / 拒绝 {round.rejected_actions}</strong>
                    <small>动作预算 {round.action_budget_used}/{round.action_budget_limit} · 外部预算 {round.external_budget_used}/{round.external_budget_limit} · 新增 Evidence {round.new_evidence_count}</small>
                    <small>{round.stop_reason ? stopReasonLabel(round.stop_reason) : "继续研究"}</small>
                  </article>
                ))}
              </div>
              <div className="claim-evidence-matrix">
                <div className="matrix-head"><span>Claim</span><span>验证</span><span>Evidence</span></div>
                {researchTrace.claim_evidence_matrix.map((row) => (
                  <div className="matrix-row" key={row.claim_key}>
                    <span><strong>{row.statement}</strong><small>{row.claim_key} · {row.status}</small></span>
                    <span>{row.verification_label ?? "待验证"}<small>{row.verification_confidence === null ? "—" : `${Math.round(row.verification_confidence * 100)}%`}</small></span>
                    <span>{row.evidence_ids.length ? row.evidence_ids.map((value) => <code key={value}>{value}</code>) : "无"}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
          {researchReview && (
            <details className="traceable-review">
              <summary>可追溯综述 · {researchReview.verified_claim_count} 条已验证 Claim · {researchReview.citation_sentence_count} 条逐句引用</summary>
              <pre>{researchReview.markdown}</pre>
            </details>
          )}
          <div className="verified-claims">
            {research.result.claims.map((claim, index) => (
              <article key={`${claim.statement}-${index}`}>
                <span>CLAIM {String(index + 1).padStart(2, "0")} · {claim.relation}</span>
                <h3>{claim.statement}</h3>
                <p>{claim.semantic_verification}</p>
                {!!claim.evidence.length && <ol className="claim-citations">{claim.evidence.map((evidence) => <li key={evidence.evidence_id}>{evidence.formatted_citation ?? evidence.citation_label ?? "文献元数据不完整"}<code>{evidence.evidence_id}</code></li>)}</ol>}
              </article>
            ))}
          </div>
          {!!research.result.gaps.length && <div className="gap-note"><strong>证据缺口</strong>{research.result.gaps.join("；")}</div>}
          {!!research.result.unverified_hypotheses.length && <div className="gap-note"><strong>未验证假说（不属于已证实结论）</strong>{research.result.unverified_hypotheses.join("；")}</div>}
        </section>
      )}
    </>
  );
}

function questionTypeLabel(value: ResearchRouting["question_type"]) {
  return {
    identifier: "标识符定位",
    comparison: "比较问题",
    mechanism: "因果/机制问题",
    pico: "临床 PICO",
    methodology: "方法学问题",
    systematic_review: "系统综述问题",
    latest_progress: "最新进展问题",
    general: "一般研究问题",
  }[value];
}

function strategyLabel(value: ResearchRouting["strategy"]) {
  return {
    direct: "Direct Search",
    decomposition: "Decomposition",
    deep_research: "Deep Research",
  }[value];
}

function AuditQueryGroup({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <strong>{label}</strong>
      {values.length ? values.map((value, index) => <span key={`${label}-${index}`}>{value}</span>) : <small>本轮无新增项</small>}
    </div>
  );
}

function planSourceLabel(value: string) {
  return {
    deterministic: "确定性计划",
    deterministic_plus_model: "确定性 + 模型探索",
    model: "模型扩展",
    fallback: "确定性降级",
  }[value] ?? value;
}

function stepTypeLabel(value: string) {
  return {
    route: "确定性路由",
    plan: "探索计划",
    replan: "二轮重规划",
    retrieve: "白名单执行",
    judge: "充分性判断",
    verify: "证据流水线校验",
  }[value] ?? value;
}

function actionTypeLabel(value: string) {
  return {
    local_hybrid_search: "本地混合检索",
    graph_local_search: "Graph Local",
    graph_global_search: "Graph Global",
    graph_drift_search: "Graph DRIFT",
    graph_path_search: "Graph Path",
    citation_landscape: "引用景观",
    scholarly_discovery: "四来源学术发现",
    controlled_web_search: "受控网页上下文",
    stop_research: "停止研究",
  }[value] ?? value;
}

function rejectionLabel(value: string | null) {
  return {
    action_not_allowed: "动作不在后端白名单",
    duplicate_action_id: "动作 ID 重复",
    target_claim_required: "动作必须绑定未解决 Claim",
    target_claim_not_unresolved: "目标 Claim 不存在或已解决",
    web_not_authorized: "未授权网页搜索",
    scholarly_discovery_not_authorized: "未授权学术发现",
    auto_import_not_authorized: "未授权自动入库",
    external_request_budget_exhausted: "外部请求预算已用尽",
    action_budget_exhausted: "本轮动作预算已用尽",
    blank_query: "查询为空",
    duplicate_query: "查询已执行",
    lower_value_or_diversity_budget: "价值或多样性优先级不足",
  }[value ?? ""] ?? value ?? "未通过策略校验";
}

function stopReasonLabel(value: string) {
  return {
    core_claims_and_counterevidence_covered: "核心声明与反证已覆盖",
    round_budget_exhausted: "轮次预算耗尽",
    no_new_evidence: "没有新增证据",
    high_value_evidence_gaps_remain: "仍有高价值证据缺口",
  }[value] ?? value;
}
