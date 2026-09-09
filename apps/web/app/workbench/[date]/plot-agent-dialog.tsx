"use client";

import { BarChart3, Eye, EyeOff, LoaderCircle, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";

import { API_URL, type WorkbenchAttachment, type WorkbenchPlotStyle } from "@/lib/api";

type PlotAgentDialogProps = {
  open: boolean;
  editing: WorkbenchAttachment | null;
  table: string;
  style: WorkbenchPlotStyle;
  busy: boolean;
  error: string;
  projectId: string;
  onClose: () => void;
  onTableChange: (value: string) => void;
  onStyleChange: (value: WorkbenchPlotStyle) => void;
  onSubmit: () => void;
};

const CHARTS: Array<[WorkbenchPlotStyle["chart_type"], string]> = [
  ["auto", "自动识别"],
  ["bar", "柱状图"],
  ["line", "折线图"],
  ["scatter", "散点图"],
  ["distribution", "分布图"],
  ["heatmap", "热图"],
];

function LocalPlotPreview({ src }: Readonly<{ src: string }>) {
  // Blob URLs are local, ephemeral previews and cannot use the Next image optimizer.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="科研图实时预览" />;
}

export function PlotAgentDialog({
  open,
  editing,
  table,
  style,
  busy,
  error,
  projectId,
  onClose,
  onTableChange,
  onStyleChange,
  onSubmit,
}: Readonly<PlotAgentDialogProps>) {
  const [previewEnabled, setPreviewEnabled] = useState(true);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const planningSource = editing?.render_spec.planning_source;
  const modelRationale = editing?.render_spec.model_rationale;
  const detectedLayout = editing?.render_spec.data_layout;
  const replicateStats = Array.isArray(editing?.render_spec.replicate_stats)
    ? editing.render_spec.replicate_stats
    : [];
  const update = <K extends keyof WorkbenchPlotStyle>(key: K, value: WorkbenchPlotStyle[K]) => {
    onStyleChange({ ...style, [key]: value });
  };
  useEffect(() => {
    if (!open || !previewEnabled || !projectId || table.trim().length < 10) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setPreviewing(true);
      setPreviewError("");
      try {
        const response = await fetch(`${API_URL}/workbench/plots/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...style, allow_model_planning: false, markdown_table: table, project_id: projectId }),
          signal: controller.signal,
        });
        if (!response.ok) {
          const payload = await response.json() as { detail?: string };
          throw new Error(payload.detail ?? `预览失败（${response.status}）`);
        }
        const objectUrl = URL.createObjectURL(await response.blob());
        setPreviewUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return objectUrl;
        });
      } catch (caught) {
        if (!controller.signal.aborted) {
          setPreviewError(caught instanceof Error ? caught.message : "预览失败");
        }
      } finally {
        if (!controller.signal.aborted) setPreviewing(false);
      }
    }, 450);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, previewEnabled, projectId, style, table]);
  if (!open) return null;
  return (
    <div className="dialog-backdrop plot-dialog-backdrop" role="presentation">
      <section className="plot-agent-dialog" role="dialog" aria-modal="true" aria-label="科研绘图 Agent">
        <header>
          <div><span className="eyebrow">SCIENTIFIC PLOT AGENT</span><h2>{editing ? "编辑科研图" : "表格可视化"}</h2></div>
          <button onClick={onClose} aria-label="关闭"><X size={17} /></button>
        </header>
        <p className="plot-agent-boundary"><Sparkles size={15} /> LLM 只生成受限的图形规划 JSON；PNG 始终由本地 Matplotlib 渲染，不执行模型代码。</p>
        {error && <div className="plot-dialog-error" role="alert">{error}</div>}
        {editing && (
          <div className={`plot-planning-status ${planningSource === "model" ? "model" : "deterministic"}`}>
            <strong>{planningSource === "model" ? "上次重绘已应用 LLM 规划" : "上次使用本地确定性规划"}</strong>
            <span>{typeof modelRationale === "string" && modelRationale ? modelRationale : "未保存模型规划说明。"}</span>
            <small>
              数据布局：{detectedLayout === "long" ? "长格式重复" : detectedLayout === "wide" ? "宽格式重复" : "汇总/普通表格"}
              {replicateStats.length ? ` · 已识别 ${replicateStats.length} 个实验组` : " · 未识别原始重复汇总"}
            </small>
            {replicateStats.length > 0 && (
              <small className="plot-replicate-summary">
                {replicateStats.map((item) => {
                  const value = item as Record<string, unknown>;
                  return `${String(value.condition ?? "组别")} n=${String(value.n ?? "?")}`;
                }).join(" · ")}
              </small>
            )}
          </div>
        )}
        <div className="plot-agent-grid">
          <div className="plot-agent-data">
            <label>Markdown 数据表<textarea value={table} onChange={(event) => onTableChange(event.target.value)} placeholder="| 组别 | 重复编号 | 数值 |\n| --- | --- | --- |\n| 对照 | B1 | 1.2 |" /></label>
            <label>绘图意图<textarea value={style.intent} onChange={(event) => update("intent", event.target.value)} placeholder="例如：突出不同处理组均值差异，显示标准差，使用适合论文的配色。" /></label>
            <label className="plot-model-consent"><input type="checkbox" checked={style.allow_model_planning} onChange={(event) => update("allow_model_planning", event.target.checked)} /> 允许把当前表格发送给已配置 LLM 做意图识别和样式建议</label>
          </div>
          <div className="plot-style-controls">
            <label>图形类型<select value={style.chart_type} onChange={(event) => update("chart_type", event.target.value as WorkbenchPlotStyle["chart_type"])}>{CHARTS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label>标题<input value={style.title} onChange={(event) => update("title", event.target.value)} /></label>
            <label>图注<input value={style.caption} onChange={(event) => update("caption", event.target.value)} /></label>
            <label>配色<select value={style.palette} onChange={(event) => update("palette", event.target.value as WorkbenchPlotStyle["palette"])}><option value="journal">通用期刊</option><option value="npg">NPG 风格</option><option value="nejm">NEJM 风格</option><option value="lancet">Lancet 风格</option><option value="jama">JAMA 风格</option><option value="colorblind">色觉友好</option></select></label>
            <label>字体 {style.font_size}px<input type="range" min={7} max={28} value={style.font_size} onChange={(event) => update("font_size", Number(event.target.value))} /></label>
            <label>线宽 {style.line_width.toFixed(1)}<input type="range" min={0.5} max={6} step={0.1} value={style.line_width} onChange={(event) => update("line_width", Number(event.target.value))} /></label>
            <label>点大小 {style.point_size}<input type="range" min={8} max={160} step={2} value={style.point_size} onChange={(event) => update("point_size", Number(event.target.value))} /></label>
            <div className="plot-size-grid"><label>宽度<input type="number" min={4} max={16} step={0.2} value={style.figure_width} onChange={(event) => update("figure_width", Number(event.target.value))} /></label><label>高度<input type="number" min={3} max={12} step={0.2} value={style.figure_height} onChange={(event) => update("figure_height", Number(event.target.value))} /></label><label>DPI<input type="number" min={120} max={600} step={30} value={style.dpi} onChange={(event) => update("dpi", Number(event.target.value))} /></label></div>
            <label>图例<select value={style.legend_position} onChange={(event) => update("legend_position", event.target.value as WorkbenchPlotStyle["legend_position"])}><option value="best">自动</option><option value="top">顶部</option><option value="bottom">底部</option><option value="left">左侧</option><option value="right">右侧</option><option value="none">隐藏</option></select></label>
            <label className="plot-model-consent"><input type="checkbox" checked={style.show_grid} onChange={(event) => update("show_grid", event.target.checked)} /> 显示横向参考网格</label>
          </div>
        </div>
        <section className="plot-replicate-controls">
          <header><div><span className="eyebrow">REPLICATE-AWARE</span><h3>重复实验与统计</h3></div><span>默认按独立生物学重复处理</span></header>
          <div>
            <label>数据组织<select value={style.data_layout} onChange={(event) => update("data_layout", event.target.value as WorkbenchPlotStyle["data_layout"])}><option value="auto">自动识别</option><option value="long">长格式：每行一个重复</option><option value="wide">宽格式：重复分列</option><option value="summary">已汇总：均值 + 误差列</option></select></label>
            <label>条件/组别列<input value={style.condition_column} onChange={(event) => update("condition_column", event.target.value)} placeholder="留空自动识别" /></label>
            <label>测量值列<input value={style.value_column} onChange={(event) => update("value_column", event.target.value)} placeholder="长格式填写，例如 数值" /></label>
            <label>重复/受试对象列<input value={style.replicate_column} onChange={(event) => update("replicate_column", event.target.value)} placeholder="配对数据必须填写" /></label>
            <label className="wide">宽格式重复列<input value={style.replicate_columns.join(", ")} onChange={(event) => update("replicate_columns", event.target.value.split(",").map((value) => value.trim()).filter(Boolean))} placeholder="Rep1, Rep2, Rep3" /></label>
            <label>汇总方式<select value={style.summary_stat} onChange={(event) => update("summary_stat", event.target.value as WorkbenchPlotStyle["summary_stat"])}><option value="mean_sd">均值 ± 样本 SD</option><option value="mean_sem">均值 ± SEM</option><option value="mean_ci95">均值 + 95% t 区间</option></select></label>
            <label>重复类型<select value={style.replicate_unit} onChange={(event) => update("replicate_unit", event.target.value as WorkbenchPlotStyle["replicate_unit"])}><option value="biological">生物学重复</option><option value="technical">技术重复</option></select></label>
            <label>样本关系<select value={style.pairing_mode} onChange={(event) => update("pairing_mode", event.target.value as WorkbenchPlotStyle["pairing_mode"])}><option value="independent">组间独立</option><option value="paired">配对/重复测量</option></select></label>
            <label className="plot-model-consent"><input type="checkbox" checked={style.show_all_points} onChange={(event) => update("show_all_points", event.target.checked)} /> 叠加全部原始重复点</label>
            <label className="plot-model-consent"><input type="checkbox" checked={style.show_sample_size} onChange={(event) => update("show_sample_size", event.target.checked)} /> 标注每组 n</label>
          </div>
          <p>生物学重复代表独立实验单位；技术重复只反映测量精密度。系统不自动进行显著性检验，也不会把技术复孔当作独立生物学样本。</p>
        </section>
        <section className="plot-live-preview">
          <header><div><span className="eyebrow">LOCAL PREVIEW</span><h3>图片预览</h3></div><button type="button" onClick={() => setPreviewEnabled((value) => !value)}>{previewEnabled ? <Eye size={14} /> : <EyeOff size={14} />}{previewEnabled ? "预览已开启" : "预览已关闭"}</button></header>
          {previewEnabled && <div>{previewing && <span className="plot-preview-state"><LoaderCircle className="spin" size={15} /> 正在本地更新预览…</span>}{previewError && <span className="plot-preview-error">{previewError}</span>}{previewUrl && <LocalPlotPreview src={previewUrl} />}{!previewing && !previewError && !previewUrl && <span className="plot-preview-state">输入有效表格后自动预览</span>}</div>}
          {style.allow_model_planning && <p className="plot-preview-note"><strong>当前预览不代表 LLM 结果。</strong> 实时预览只使用本地规则；点击生成/重新渲染后才调用一次 LLM。即使未填写绘图意图，系统也会根据表格自动建议图形和样式。</p>}
        </section>
        <footer><span>{editing ? `当前修订 V${editing.render_revision}` : "支持 GFM Markdown 表格，最多 5000 行"}</span><button onClick={onClose}>取消</button><button className="primary" disabled={busy || (!editing && table.trim().length < 10)} onClick={onSubmit}>{busy ? <LoaderCircle className="spin" size={15} /> : <BarChart3 size={15} />}{busy && style.allow_model_planning ? "LLM 规划并渲染中" : editing ? "重新渲染" : "生成并插入笔记"}</button></footer>
      </section>
    </div>
  );
}
