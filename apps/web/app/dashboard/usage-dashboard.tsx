"use client";

import {
  Activity,
  BadgeDollarSign,
  Box,
  ChartNoAxesCombined,
  CircleGauge,
  Clock3,
  Coins,
  DatabaseZap,
  LoaderCircle,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { apiRequest, type LibraryResponse, type UsageDashboard } from "@/lib/api";

type Period = UsageDashboard["period"];
type Granularity = UsageDashboard["granularity"];
type Metric = "total_tokens" | "request_count" | "cache_hit_rate" | "known_cost_usd";

const metricCopy: Record<Metric, { label: string; color: string }> = {
  total_tokens: { label: "Token", color: "#246b52" },
  request_count: { label: "请求", color: "#3c7ea6" },
  cache_hit_rate: { label: "命中率", color: "#a97933" },
  known_cost_usd: { label: "已知成本", color: "#71559a" },
};

function compact(value: number) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function TrendChart({ data, metric }: { data: UsageDashboard["series"]; metric: Metric }) {
  const width = 920;
  const height = 260;
  const padding = 28;
  const values = data.map((item) => metric === "cache_hit_rate" ? item[metric] * 100 : item[metric]);
  const maximum = Math.max(...values, 1);
  const points = values.map((value, index) => {
    const x = data.length <= 1 ? width / 2 : padding + (index * (width - padding * 2)) / (data.length - 1);
    const y = height - padding - (value / maximum) * (height - padding * 2);
    return { x, y, value };
  });
  const path = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const area = points.length ? `${path} L${points.at(-1)?.x},${height - padding} L${points[0].x},${height - padding} Z` : "";
  return <div className="usage-chart-canvas">
    {!data.length ? <div className="dashboard-empty"><ChartNoAxesCombined size={25} />统计将在产生新的模型请求后显示。</div> : <>
      <svg aria-label={`${metricCopy[metric].label}趋势图`} role="img" viewBox={`0 0 ${width} ${height}`}>
        {[0, .25, .5, .75, 1].map((ratio) => <line key={ratio} x1={padding} x2={width - padding} y1={padding + ratio * (height - padding * 2)} y2={padding + ratio * (height - padding * 2)} />)}
        <path className="usage-chart-area" d={area} style={{ fill: `${metricCopy[metric].color}18` }} />
        <path className="usage-chart-line" d={path} style={{ stroke: metricCopy[metric].color }} />
        {points.map((point, index) => <circle key={data[index].bucket} cx={point.x} cy={point.y} fill={metricCopy[metric].color} r="4"><title>{data[index].bucket} · {point.value.toFixed(metric === "known_cost_usd" ? 4 : 1)}</title></circle>)}
      </svg>
      <div className="usage-chart-labels">{data.map((item, index) => index % Math.max(1, Math.ceil(data.length / 7)) === 0 ? <span key={item.bucket}>{item.bucket}</span> : null)}</div>
    </>}
  </div>;
}

export function UsageDashboardView() {
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [dashboard, setDashboard] = useState<UsageDashboard | null>(null);
  const [period, setPeriod] = useState<Period>("30d");
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [metric, setMetric] = useState<Metric>("total_tokens");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const projectId = library?.project_id;
  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams({ project_id: projectId, period, granularity });
      setDashboard(await apiRequest<UsageDashboard>(`/dashboard?${params}`, { cache: "no-store" }));
    } catch (value) { setError(value instanceof Error ? value.message : "仪表盘加载失败"); }
    finally { setLoading(false); }
  }, [granularity, period, projectId]);

  useEffect(() => {
    let active = true;
    apiRequest<LibraryResponse>("/library", { cache: "no-store" })
      .then((value) => { if (active) setLibrary(value); })
      .catch((value: unknown) => {
        if (active) {
          setError(value instanceof Error ? value.message : "项目加载失败");
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    const params = new URLSearchParams({ project_id: projectId, period, granularity });
    apiRequest<UsageDashboard>(`/dashboard?${params}`, { cache: "no-store" })
      .then((value) => {
        if (active) {
          setDashboard(value);
          setError(null);
          setLoading(false);
        }
      })
      .catch((value: unknown) => {
        if (active) {
          setError(value instanceof Error ? value.message : "仪表盘加载失败");
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, [granularity, period, projectId]);

  const peak = useMemo(() => dashboard?.series.reduce((best, point) => point.total_tokens > best.total_tokens ? point : best, dashboard.series[0]) ?? null, [dashboard]);
  const summary = dashboard?.summary;
  return <div className="usage-dashboard-page">
    <header className="page-header usage-dashboard-header">
      <div><span className="eyebrow">MODEL OPERATIONS OBSERVATORY</span><h1>用量仪表盘</h1><p>统一观察模型请求、Token、检索缓存与厂商返回的实际成本，支持不同时间粒度。</p></div>
      <div className="dashboard-live-badge"><Activity size={14} /><span>本地用量账本</span></div>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="dashboard-toolbar">
      <div className="dashboard-segment" aria-label="统计时间">{(["7d", "30d", "90d", "all"] as Period[]).map((value) => <button className={period === value ? "active" : ""} key={value} onClick={() => setPeriod(value)}>{value === "all" ? "全部" : value}</button>)}</div>
      <label><Clock3 size={14} />聚合<select value={granularity} onChange={(event) => setGranularity(event.target.value as Granularity)}><option value="day">按日</option><option value="week">按周</option><option value="month">按月</option></select></label>
      <button className="dashboard-refresh" disabled={loading || !projectId} onClick={() => void load()}>{loading ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}刷新</button>
    </section>
    <section className="usage-kpi-grid">
      <article className="token"><div><Coins size={18} /></div><span>总 Token</span><strong>{compact(summary?.total_tokens ?? 0)}</strong><small>输入 {compact(summary?.prompt_tokens ?? 0)} · 输出 {compact(summary?.completion_tokens ?? 0)}</small></article>
      <article className="requests"><div><Sparkles size={18} /></div><span>模型请求</span><strong>{(summary?.request_count ?? 0).toLocaleString()}</strong><small>{summary?.estimated_token_events ?? 0} 次 Token 为本地估算</small></article>
      <article className="cache"><div><DatabaseZap size={18} /></div><span>缓存命中率</span><strong>{((summary?.cache_hit_rate ?? 0) * 100).toFixed(1)}%</strong><small>{summary?.cache_hits ?? 0}/{summary?.retrieval_count ?? 0} 次检索命中</small></article>
      <article className="cost"><div><BadgeDollarSign size={18} /></div><span>已知成本</span><strong>${(summary?.known_cost_usd ?? 0).toFixed(4)}</strong><small>覆盖 {((summary?.cost_coverage_rate ?? 0) * 100).toFixed(0)}% 请求 · 未覆盖不估价</small></article>
    </section>
    <section className="dashboard-main-grid">
      <article className="usage-trend-card">
        <header><div><span className="eyebrow">TIME SERIES</span><h2>{metricCopy[metric].label}趋势</h2></div><div className="metric-tabs">{(Object.keys(metricCopy) as Metric[]).map((value) => <button className={metric === value ? "active" : ""} key={value} onClick={() => setMetric(value)}>{metricCopy[value].label}</button>)}</div></header>
        <TrendChart data={dashboard?.series ?? []} metric={metric} />
        <footer><span><CircleGauge size={13} />峰值区间 {peak?.bucket ?? "暂无"}</span><span><Box size={13} />统计项目 {library?.project_name ?? "加载中"}</span></footer>
      </article>
      <aside className="dashboard-insight-card"><header><CircleGauge size={19} /><div><span className="eyebrow">OBSERVATIONS</span><h2>运行洞察</h2></div></header><div className="insight-orbit"><div><strong>{compact(summary?.cached_tokens ?? 0)}</strong><span>缓存 Token</span></div></div><ul><li><strong>成本透明度</strong><span>仅累加厂商响应明确返回的成本，不使用猜测单价。</span></li><li><strong>Token 可信度</strong><span>{summary?.estimated_token_events ? `${summary.estimated_token_events} 次请求缺少 usage，已按字符数明确标记估算。` : "当前记录均来自厂商 usage。"}</span></li><li><strong>检索效率</strong><span>{summary?.retrieval_count ? `共 ${summary.retrieval_count} 次检索，${summary.cache_hits} 次复用缓存。` : "暂无检索记录。"}</span></li></ul></aside>
    </section>
    <section className="model-usage-card"><header><div><span className="eyebrow">MODEL BREAKDOWN</span><h2>模型用量分布</h2></div><span>{dashboard?.models.length ?? 0} 个模型</span></header><div className="model-usage-table"><div className="table-head"><span>模型</span><span>请求</span><span>Token</span><span>命中 Token</span><span>已知成本</span></div>{dashboard?.models.map((item) => <div className="table-row" key={`${item.provider}-${item.model}`}><span><i /> <b>{item.model}</b><small>{item.provider}</small></span><span>{item.request_count}</span><span>{item.total_tokens.toLocaleString()}</span><span>{item.cached_tokens.toLocaleString()}</span><span>${item.known_cost_usd.toFixed(4)}</span></div>)}{!dashboard?.models.length && <div className="dashboard-table-empty">暂无模型用量；后续请求会自动记录。</div>}</div></section>
  </div>;
}