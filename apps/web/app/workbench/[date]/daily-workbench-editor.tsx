"use client";

import {
  ArrowLeft,
  BarChart3,
  Check,
  ClipboardCopy,
  FileImage,
  Heading2,
  ImagePlus,
  Heading1,
  Heading3,
  ListChecks,
  LoaderCircle,
  Pilcrow,
  Redo2,
  Save,
  Sparkles,
  Table2,
  TextQuote,
  Trash2,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { MarkdownEditor, type MarkdownEditorHandle } from "./markdown-editor";
import { PlotAgentDialog } from "./plot-agent-dialog";

import {
  API_URL,
  apiRequest,
  type LibraryResponse,
  type WorkbenchAttachment,
  type WorkbenchDay,
  type WorkbenchPlotResponse,
  type WorkbenchPlotStyle,
  type WorkbenchTask,
} from "@/lib/api";

const STARTER = "# 研究目标\n\n\n## 今日记录\n\n\n## 数据与观察\n\n\n## 下一步\n\n- [ ] ";
const DEFAULT_PLOT_STYLE: WorkbenchPlotStyle = {
  chart_type: "auto",
  title: "科研数据可视化",
  caption: "",
  intent: "",
  allow_model_planning: false,
  font_size: 10,
  palette: "journal",
  line_width: 1.8,
  point_size: 32,
  figure_width: 7.2,
  figure_height: 4.6,
  dpi: 300,
  show_grid: false,
  legend_position: "best",
  data_layout: "auto",
  condition_column: "",
  value_column: "",
  replicate_column: "",
  replicate_columns: [],
  summary_stat: "mean_sd",
  show_all_points: true,
  show_sample_size: true,
  replicate_unit: "biological",
  pairing_mode: "independent",
};

function replaceFirstExact(value: string, search: string, replacement: string) {
  const index = value.indexOf(search);
  if (index < 0) return value;
  return `${value.slice(0, index)}${replacement}${value.slice(index + search.length)}`;
}

export function DailyWorkbenchEditor({ entryDate }: Readonly<{ entryDate: string }>) {
  const [projectId, setProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [markdown, setMarkdown] = useState(STARTER);
  const [tasks, setTasks] = useState<WorkbenchTask[]>([]);
  const [attachments, setAttachments] = useState<WorkbenchAttachment[]>([]);
  const [mode, setMode] = useState<"split" | "source" | "preview">("preview");
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState("");
  const [uploading, setUploading] = useState(false);
  const [plotDialogOpen, setPlotDialogOpen] = useState(false);
  const [plotTable, setPlotTable] = useState("");
  const [plotStyle, setPlotStyle] = useState(DEFAULT_PLOT_STYLE);
  const [plotting, setPlotting] = useState(false);
  const [editingPlot, setEditingPlot] = useState<WorkbenchAttachment | null>(null);
  const [editorLoaded, setEditorLoaded] = useState(false);
  const [error, setError] = useState("");
  const [plotNotice, setPlotNotice] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const previewEditorRef = useRef<MarkdownEditorHandle>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const hydratedRef = useRef(false);
  const markdownRef = useRef(markdown);
  const generatedPlotAttachments = useMemo(
    () => attachments.filter((item) => item.attachment_kind === "generated_plot"),
    [attachments],
  );

  const updateMarkdown = useCallback((value: string) => {
    markdownRef.current = value;
    setMarkdown(value);
  }, []);

  const save = useCallback(async (targetProjectId = projectId, silent = false) => {
    if (!targetProjectId || !title.trim()) return;
    if (!silent) setSaving(true);
    try {
      await apiRequest(`/workbench/days/${entryDate}`, {
        method: "PUT",
        body: JSON.stringify({ project_id: targetProjectId, title, content_markdown: markdown }),
      });
      setSavedAt(new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date()));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }, [entryDate, markdown, projectId, title]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const library = await apiRequest<LibraryResponse>("/library");
        const day = await apiRequest<WorkbenchDay>(`/workbench/days/${entryDate}?project_id=${library.project_id}`);
        if (!active) return;
        setProjectId(library.project_id);
        setTitle(day.note?.title ?? `${entryDate} 科研笔记`);
        const loadedMarkdown = day.note?.content_markdown || STARTER;
        updateMarkdown(loadedMarkdown);
        setTasks(day.tasks);
        setAttachments(day.attachments);
        setEditorLoaded(true);
        window.setTimeout(() => { hydratedRef.current = true; }, 0);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "笔记加载失败");
      }
    }
    void load();
    return () => { active = false; };
  }, [entryDate, updateMarkdown]);

  useEffect(() => {
    if (!hydratedRef.current || !projectId || !title.trim()) return;
    const timer = window.setTimeout(() => void save(projectId, true), 1200);
    return () => window.clearTimeout(timer);
  }, [markdown, projectId, save, title]);

  useEffect(() => {
    function handleDocumentShortcut(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      } else if (event.key === "/") {
        event.preventDefault();
        setMode((value) => value === "source" ? "preview" : "source");
      }
    }
    document.addEventListener("keydown", handleDocumentShortcut);
    return () => document.removeEventListener("keydown", handleDocumentShortcut);
  }, [save]);

  function insertMarkdown(prefix: string, suffix = "", placeholder = "") {
    const textarea = textareaRef.current;
    if (!textarea) {
      const inserted = `${prefix}${placeholder}${suffix}`;
      if (mode === "preview") {
        previewEditorRef.current?.insertMarkdown(inserted);
      } else {
        updateMarkdown(`${markdownRef.current}${inserted}`);
      }
      return;
    }
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const currentMarkdown = markdownRef.current;
    const selected = currentMarkdown.slice(start, end) || placeholder;
    const next = `${currentMarkdown.slice(0, start)}${prefix}${selected}${suffix}${currentMarkdown.slice(end)}`;
    updateMarkdown(next);
    window.setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(start + prefix.length, start + prefix.length + selected.length);
    }, 0);
  }

  function changeBlockType(level: 0 | 1 | 2 | 3) {
    if (mode === "preview") {
      previewEditorRef.current?.setBlockType(level);
      return;
    }
    if (level === 0) return;
    insertMarkdown(`${"#".repeat(level)} `, "", `${level === 1 ? "一级" : level === 2 ? "二级" : "三级"}标题`);
  }

  function undo() {
    if (mode === "preview") {
      previewEditorRef.current?.undo();
      return;
    }
    textareaRef.current?.focus();
    document.execCommand("undo");
  }

  function redo() {
    if (mode === "preview") {
      previewEditorRef.current?.redo();
      return;
    }
    textareaRef.current?.focus();
    document.execCommand("redo");
  }

  async function uploadImage(file: File) {
    if (!projectId) return;
    setUploading(true);
    setError("");
    const form = new FormData();
    form.append("project_id", projectId);
    form.append("run_ocr", "true");
    form.append("file", file);
    try {
      const attachment = await apiRequest<WorkbenchAttachment>(`/workbench/days/${entryDate}/attachments`, { method: "POST", body: form });
      const imageUrl = `${API_URL}${attachment.content_url.replace("/api/v1", "")}`;
      insertMarkdown(`\n![${attachment.filename}](${imageUrl})\n`);
      setAttachments((value) => value.some((item) => item.id === attachment.id) ? value : [...value, attachment]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "图片上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function deleteAttachment(attachment: WorkbenchAttachment) {
    try {
      await apiRequest(`/workbench/attachments/${attachment.id}?project_id=${projectId}`, { method: "DELETE" });
      setAttachments((value) => value.filter((item) => item.id !== attachment.id));
      if (attachment.attachment_kind === "generated_plot") {
        const escapedId = attachment.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const imagePattern = new RegExp(
          `\\n?!\\[[^\\]]*\\]\\([^\\n)]*/workbench/attachments/${escapedId}/content(?:\\?v=\\d+)?\\)\\n?`,
        );
        const next = markdownRef.current.replace(imagePattern, "\n");
        updateMarkdown(next);
        previewEditorRef.current?.setMarkdown(next);
        await apiRequest(`/workbench/days/${entryDate}`, {
          method: "PUT",
          body: JSON.stringify({ project_id: projectId, title, content_markdown: next }),
        });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "图片删除失败");
    }
  }

  async function deleteNote() {
    if (!window.confirm("确定删除这一天的科研笔记及其图片吗？当日任务会保留。")) return;
    try {
      await apiRequest(`/workbench/days/${entryDate}?project_id=${projectId}`, {
        method: "DELETE",
      });
      window.location.href = "/workbench";
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "笔记删除失败");
    }
  }

  function insertOcrText(attachment: WorkbenchAttachment) {
    insertMarkdown(`\n> **OCR · ${attachment.filename}**\n> ${attachment.ocr_text.replaceAll("\n", "\n> ")}\n`);
  }

  function firstMarkdownTable(value: string) {
    const lines = value.split("\n");
    for (let index = 0; index < lines.length - 2; index += 1) {
      if (!lines[index].trim().startsWith("|") || !lines[index + 1].includes("---")) continue;
      const table: string[] = [];
      for (let cursor = index; cursor < lines.length && lines[cursor].trim().startsWith("|"); cursor += 1) table.push(lines[cursor]);
      if (table.length >= 3) return table.join("\n");
    }
    return "";
  }

  function openPlotAgent(attachment: WorkbenchAttachment | null = null) {
    setError("");
    setPlotNotice("");
    setEditingPlot(attachment);
    if (attachment) {
      const spec = attachment.render_spec;
      setPlotStyle({
        ...DEFAULT_PLOT_STYLE,
        chart_type: (spec.chart_type as WorkbenchPlotStyle["chart_type"]) ?? "auto",
        title: (spec.title as string) ?? attachment.caption ?? "科研数据可视化",
        caption: attachment.caption ?? "",
        intent: (spec.intent as string) ?? "",
        allow_model_planning: spec.allow_model_planning === undefined
          ? spec.planning_source === "model"
          : Boolean(spec.allow_model_planning),
        font_size: Number(spec.font_size ?? 10),
        palette: ((spec.palette_name ?? spec.palette) as WorkbenchPlotStyle["palette"]) ?? "journal",
        line_width: Number(spec.line_width ?? 1.8),
        point_size: Number(spec.point_size ?? 32),
        figure_width: Number(spec.figure_width ?? 7.2),
        figure_height: Number(spec.figure_height ?? 4.6),
        dpi: Number(spec.dpi ?? 300),
        show_grid: Boolean(spec.show_grid),
        legend_position: (spec.legend_position as WorkbenchPlotStyle["legend_position"]) ?? "best",
        data_layout: (spec.data_layout as WorkbenchPlotStyle["data_layout"]) ?? "auto",
        condition_column: (spec.condition_column as string) ?? "",
        value_column: (spec.value_column as string) ?? "",
        replicate_column: (spec.replicate_column as string) ?? "",
        replicate_columns: Array.isArray(spec.replicate_columns) ? spec.replicate_columns.map(String) : [],
        summary_stat: (spec.summary_stat as WorkbenchPlotStyle["summary_stat"]) ?? "mean_sd",
        show_all_points: spec.show_all_points === undefined ? true : Boolean(spec.show_all_points),
        show_sample_size: spec.show_sample_size === undefined ? true : Boolean(spec.show_sample_size),
        replicate_unit: (spec.replicate_unit as WorkbenchPlotStyle["replicate_unit"]) ?? "biological",
        pairing_mode: (spec.pairing_mode as WorkbenchPlotStyle["pairing_mode"]) ?? "independent",
      });
      setPlotTable(attachment.source_table_markdown ?? "");
    } else {
      setPlotStyle(DEFAULT_PLOT_STYLE);
      setPlotTable(firstMarkdownTable(markdownRef.current));
    }
    setPlotDialogOpen(true);
  }

  async function submitPlot() {
    if (!projectId) return;
    setPlotting(true);
    setError("");
    try {
      const payload = { ...plotStyle, markdown_table: plotTable, project_id: projectId };
      const response = editingPlot
        ? await apiRequest<WorkbenchPlotResponse>(`/workbench/attachments/${editingPlot.id}/plot?project_id=${projectId}`, { method: "PATCH", body: JSON.stringify({ ...plotStyle, markdown_table: plotTable }) })
        : await apiRequest<WorkbenchPlotResponse>(`/workbench/days/${entryDate}/plots`, { method: "POST", body: JSON.stringify(payload) });
      const attachment = response.attachment;
      const planningNotice = response.planning_source === "model"
        ? `LLM 图形规划已应用${response.model_rationale ? `：${response.model_rationale}` : "。"}`
        : plotStyle.allow_model_planning
          ? response.model_rationale || "LLM 规划未应用，已回退到本地确定性绘图。"
          : "已使用本地确定性绘图。";
      const warningNotice = response.warnings.length
        ? ` 绘图提示：${response.warnings.join("；")}`
        : "";
      setPlotNotice(`${planningNotice}${warningNotice}`);
      setAttachments((items) => {
        const existing = items.findIndex((item) => item.id === attachment.id);
        return existing < 0 ? [...items, attachment] : items.map((item) => item.id === attachment.id ? attachment : item);
      });
      if (!editingPlot) {
        const imageUrl = `${API_URL}${attachment.content_url.replace("/api/v1", "")}`;
        const imageMarkdown = `\n\n![${attachment.caption || attachment.filename}](${imageUrl})\n`;
        const current = markdownRef.current;
        const next = current.includes(plotTable)
          ? replaceFirstExact(current, plotTable, `${plotTable}${imageMarkdown}`)
          : `${current}${imageMarkdown}`;
        updateMarkdown(next);
        previewEditorRef.current?.setMarkdown(next);
        await apiRequest(`/workbench/days/${entryDate}`, {
          method: "PUT",
          body: JSON.stringify({ project_id: projectId, title, content_markdown: next }),
        });
      } else {
        const day = await apiRequest<WorkbenchDay>(`/workbench/days/${entryDate}?project_id=${projectId}`);
        const next = day.note?.content_markdown ?? markdownRef.current;
        updateMarkdown(next);
        previewEditorRef.current?.setMarkdown(next);
      }
      setPlotDialogOpen(false);
      setEditingPlot(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "科研绘图失败");
    } finally {
      setPlotting(false);
    }
  }

  return (
    <div className="daily-editor-page">
      <header className="daily-editor-header">
        <div>
          <Link href="/workbench"><ArrowLeft size={16} /> 返回工作台</Link>
          <span>{entryDate}</span>
        </div>
        <div className="editor-save-state">
          {saving ? <><LoaderCircle className="spin" size={14} /> 保存中</> : <><Check size={14} /> {savedAt ? `${savedAt} 已保存` : "自动保存已开启"}</>}
          <button onClick={() => void save()} disabled={saving}><Save size={15} /> 保存</button>
          <button className="danger" onClick={() => void deleteNote()} disabled={!projectId}><Trash2 size={15} /> 删除</button>
        </div>
      </header>

      {error && !plotDialogOpen && <div className="error-banner">{error}</div>}
      {plotNotice && <div className="success-banner">{plotNotice}</div>}

      <section className="notion-document">
        <div className="notion-cover"><Sparkles size={21} /><span>DAILY RESEARCH LOG</span></div>
        <input className="notion-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} aria-label="笔记标题" />
        <div className="notion-meta"><span>{tasks.length} 项当日任务</span><span>{attachments.length} 张研究图片</span><span>Markdown + GFM</span><span>本地 OCR</span><span>本地科研绘图</span></div>

        <div className="editor-toolbar">
          <button onClick={undo} title="撤销（Ctrl+Z）"><Undo2 size={15} /> 撤销</button>
          <button onClick={redo} title="重做（Ctrl+Shift+Z / Ctrl+Y）"><Redo2 size={15} /> 重做</button>
          <span className="editor-toolbar-divider" />
          <button onClick={() => changeBlockType(0)} title="转为正文（Ctrl+Shift+0）"><Pilcrow size={15} /> 正文</button>
          <button onClick={() => changeBlockType(1)} title="一级标题（Ctrl+Shift+1）"><Heading1 size={15} /> 一级</button>
          <button onClick={() => changeBlockType(2)} title="二级标题（Ctrl+Shift+2）"><Heading2 size={15} /> 二级</button>
          <button onClick={() => changeBlockType(3)} title="三级标题（Ctrl+Shift+3）"><Heading3 size={15} /> 三级</button>
          <button onClick={() => insertMarkdown("- [ ] ", "", "待办事项")}><ListChecks size={15} /> 待办</button>
          <button onClick={() => insertMarkdown("\n| 指标 | 样本 A | 样本 B |\n| --- | --- | --- |\n| 结果 |  |  |\n")}><Table2 size={15} /> 表格</button>
          <button onClick={() => openPlotAgent()}><BarChart3 size={15} /> 表格绘图</button>
          <button onClick={() => insertMarkdown("> ", "", "关键观察")}><TextQuote size={15} /> 引用</button>
          <button onClick={() => fileRef.current?.click()} disabled={uploading}>{uploading ? <LoaderCircle className="spin" size={15} /> : <ImagePlus size={15} />} 图片 + OCR</button>
          <input ref={fileRef} className="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadImage(file); }} />
          <div className="editor-mode-switch"><button className={mode === "source" ? "active" : ""} onClick={() => setMode("source")} title="源码模式（Ctrl+/）">源码</button><button className={mode === "split" ? "active" : ""} onClick={() => setMode("split")}>分栏</button><button className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")} title="预览编辑模式（Ctrl+/）">预览</button></div>
        </div>
        <div className="editor-shortcut-hint">Ctrl+Z 撤销 · Ctrl+Shift+Z / Ctrl+Y 重做 · Ctrl+S 保存 · Ctrl+/ 切换源码/预览 · 表头支持文本光标、拖选和复制 · Esc 选中当前块后退格删除 · 标题开头或空标题退格转正文 · 空列表项按 Enter / Backspace 逐级退出缩进</div>

        <div className={`markdown-workspace mode-${mode}`}>
          {mode !== "preview" && <textarea ref={textareaRef} value={markdown} onChange={(event) => updateMarkdown(event.target.value)} spellCheck placeholder="用 Markdown 记录今天的研究源码…" />}
          {mode === "split" && <article className="markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown></article>}
          {mode === "preview" && editorLoaded && (
            <div className="markdown-rich-editor">
              <MarkdownEditor
                initialMarkdown={markdown}
                onChange={updateMarkdown}
                onDeletePlot={(attachment) => void deleteAttachment(attachment)}
                onEditPlot={openPlotAgent}
                plotAttachments={generatedPlotAttachments}
                ref={previewEditorRef}
              />
            </div>
          )}
        </div>
      </section>

      <section className="daily-support-grid">
        <article className="day-task-panel">
          <header><div><span className="eyebrow">TODAY</span><h2>当日任务</h2></div><ListChecks size={19} /></header>
          {tasks.map((task) => <div className={task.status} key={task.id}><span>{task.status === "done" ? <Check size={14} /> : <ListChecks size={14} />}</span><p><strong>{task.title}</strong><small>{task.status === "done" ? "已完成" : task.status === "in_progress" ? "进行中" : "待开始"}</small></p></div>)}
          {!tasks.length && <p className="support-empty">当天没有任务，可在工作台安排。</p>}
        </article>
        <article className="ocr-attachment-panel">
          <header><div><span className="eyebrow">LOCAL OCR</span><h2>图片与识别文本</h2></div><FileImage size={19} /></header>
          <div>
            {attachments.filter((attachment) => attachment.attachment_kind === "uploaded_image").map((attachment) => (
              <article key={attachment.id} onDoubleClick={() => attachment.attachment_kind === "generated_plot" && openPlotAgent(attachment)} title={attachment.attachment_kind === "generated_plot" ? "双击修改绘图样式" : undefined}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`${API_URL}${attachment.content_url.replace("/api/v1", "")}`} alt={attachment.filename} />
                <div><strong>{attachment.filename}</strong><span className={attachment.attachment_kind === "generated_plot" ? "complete" : attachment.ocr_status}>{attachment.attachment_kind === "generated_plot" ? `绘图 Agent · V${attachment.render_revision}` : attachment.ocr_status === "complete" ? "OCR 已完成" : attachment.ocr_status === "empty" ? "未识别到文字" : attachment.ocr_status === "failed" ? "OCR 失败" : "未运行 OCR"}</span><p>{attachment.attachment_kind === "generated_plot" ? `${String(attachment.render_spec.chart_type ?? "plot")} · 双击修改字体、配色、线宽等样式` : attachment.ocr_text || attachment.ocr_error || "图片已本地保存"}</p><footer>{attachment.ocr_text && <button onClick={() => insertOcrText(attachment)}><ClipboardCopy size={13} /> 插入识别文本</button>}{attachment.attachment_kind === "generated_plot" && <button onClick={() => openPlotAgent(attachment)}><BarChart3 size={13} /> 编辑图形</button>}<button onClick={() => void deleteAttachment(attachment)}><Trash2 size={13} /> 删除</button></footer></div>
              </article>
            ))}
            {!attachments.some((attachment) => attachment.attachment_kind === "uploaded_image") && <button className="ocr-upload-empty" onClick={() => fileRef.current?.click()}><ImagePlus size={24} /><strong>上传实验图、截图或扫描笔记</strong><span>在本机使用 RapidOCR 识别中英文；生成图显示在对应表格下方</span></button>}
          </div>
        </article>
      </section>
      <PlotAgentDialog open={plotDialogOpen} editing={editingPlot} table={plotTable} style={plotStyle} busy={plotting} error={error} projectId={projectId} onClose={() => { setPlotDialogOpen(false); setEditingPlot(null); setError(""); }} onTableChange={setPlotTable} onStyleChange={setPlotStyle} onSubmit={() => void submitPlot()} />
    </div>
  );
}
