"use client";

import {
  BookOpenText,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Download,
  FilePlus2,
  FileText,
  GitBranch,
  Globe2,
  Layers3,
  LibraryBig,
  LoaderCircle,
  Network,
  PanelRightOpen,
  PenLine,
  Plus,
  RefreshCw,
  Save,
  SearchCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import Image from "next/image";
import { FormEvent, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  API_URL,
  apiRequest,
  type LibraryResponse,
  type SynthesisDetail,
  type SynthesisJob,
  type SynthesisSection,
  type SynthesisSession,
} from "@/lib/api";

type DetailTab = "manuscript" | "context" | "review";
type ContextTokens = 32768 | 65536 | 131072 | 262144 | 524288 | 1000000;

const STATUS_LABELS: Record<SynthesisSession["status"], string> = {
  draft: "待开始",
  analyzing: "理解文档",
  writing: "章节写作",
  reviewing: "全文审查",
  completed: "已完成",
  failed: "需重试",
};

function feedbackText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(feedbackText).filter(Boolean).join("；");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}：${feedbackText(item)}`)
      .filter((item) => !item.endsWith("："))
      .join("\n");
  }
  return value == null ? "" : String(value);
}

export function SynthesisWorkbench() {
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [sessions, setSessions] = useState<SynthesisSession[]>([]);
  const [detail, setDetail] = useState<SynthesisDetail | null>(null);
  const [job, setJob] = useState<SynthesisJob | null>(null);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [modelDepth, setModelDepth] = useState<SynthesisSession["model_depth"]>("deep");
  const [contextTokens, setContextTokens] = useState<ContextTokens>(131072);
  const [reviewRounds, setReviewRounds] = useState(2);
  const [includeNotes, setIncludeNotes] = useState(true);
  const [onlineLiterature, setOnlineLiterature] = useState(true);
  const [tab, setTab] = useState<DetailTab>("manuscript");
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null);
  const [sectionDraft, setSectionDraft] = useState("");
  const [textTitle, setTextTitle] = useState("");
  const [textContent, setTextContent] = useState("");
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const selectedSection = useMemo(
    () => detail?.sections.find((item) => item.id === selectedSectionId) ?? null,
    [detail?.sections, selectedSectionId],
  );
  const processing = detail
    ? ["analyzing", "writing", "reviewing"].includes(detail.status)
    : false;
  const processingSessionId = processing ? detail?.id ?? null : null;

  useEffect(() => {
    let active = true;
    apiRequest<LibraryResponse>("/library", { cache: "no-store" })
      .then(async (value) => {
        if (!active) return;
        setLibrary(value);
        const result = await apiRequest<{ sessions: SynthesisSession[] }>(
          `/synthesis/sessions?project_id=${value.project_id}`,
          { cache: "no-store" },
        );
        if (active) setSessions(result.sessions);
      })
      .catch((value: unknown) => {
        if (active) setError(value instanceof Error ? value.message : "综述工作台加载失败");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!processingSessionId) return;
    let active = true;
    const timer = window.setInterval(() => {
      void Promise.all([
        apiRequest<SynthesisDetail>(`/synthesis/sessions/${processingSessionId}`, { cache: "no-store" }),
        apiRequest<SynthesisJob | null>(`/synthesis/sessions/${processingSessionId}/job`, { cache: "no-store" }),
      ])
        .then(([next, nextJob]) => {
          if (!active) return;
          setDetail(next);
          setJob(nextJob);
          if (!["analyzing", "writing", "reviewing"].includes(next.status)) {
            setRunning(false);
            if (next.status === "completed") {
              setNotice("文档理解、章节写作与全文审查已完成。可以继续编辑章节或导出。");
              setError(null);
            } else if (next.status === "failed") {
              setError(next.error_message || nextJob?.error_message || "后台写作失败，请重试。");
            }
            if (library) void refreshSessions(library.project_id);
          }
        })
        .catch((value: unknown) => {
          if (active) setError(value instanceof Error ? value.message : "写作状态刷新失败");
        });
    }, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [processingSessionId, library]);

  async function refreshSessions(projectId: string) {
    const result = await apiRequest<{ sessions: SynthesisSession[] }>(
      `/synthesis/sessions?project_id=${projectId}`,
      { cache: "no-store" },
    );
    setSessions(result.sessions);
  }

  async function openSession(sessionId: string) {
    setError(null);
    setNotice(null);
    try {
      const [value, currentJob] = await Promise.all([
        apiRequest<SynthesisDetail>(`/synthesis/sessions/${sessionId}`, { cache: "no-store" }),
        apiRequest<SynthesisJob | null>(`/synthesis/sessions/${sessionId}/job`, { cache: "no-store" }),
      ]);
      setDetail(value);
      setJob(currentJob);
      setSelectedSectionId(null);
      setSectionDraft("");
      setTab("manuscript");
      if (value.error_message) setError(value.error_message);
    } catch (value) {
      setError(value instanceof Error ? value.message : "会话加载失败");
    }
  }

  async function createSession(event: FormEvent) {
    event.preventDefault();
    if (!library || !topic.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const value = await apiRequest<SynthesisSession>("/synthesis/sessions", {
        method: "POST",
        body: JSON.stringify({
          project_id: library.project_id,
          title: title.trim() || null,
          topic: topic.trim(),
          model_depth: modelDepth,
          max_context_tokens: contextTokens,
          max_review_rounds: reviewRounds,
          include_workbench_notes: includeNotes,
          allow_online_literature: onlineLiterature,
        }),
      });
      setTitle("");
      setTopic("");
      await refreshSessions(library.project_id);
      await openSession(value.id);
      setNotice("写作项目已创建。可先添加补充材料，再启动文档理解与写作。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "写作项目创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function uploadSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const input = event.currentTarget.elements.namedItem("source-file") as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (!files.length) return;
    if (files.length > 50) {
      setError("单次最多补充 50 个文件，请分批上传。");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const failed: string[] = [];
      let imported = 0;
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        try {
          await apiRequest(`/synthesis/sessions/${detail.id}/sources/upload`, {
            method: "POST",
            body: form,
          });
          imported += 1;
        } catch (value) {
          const reason = value instanceof Error ? value.message : "解析失败";
          failed.push(`${file.name}：${reason}`);
        }
      }
      input.value = "";
      await openSession(detail.id);
      if (library) await refreshSessions(library.project_id);
      if (failed.length) {
        setError(`有 ${failed.length} 个文件未导入：${failed.join("；")}`);
      }
      setNotice(`已处理 ${files.length} 个文件，成功纳入 ${imported} 个；写作时按章节召回相关片段。`);
    } catch (value) {
      setError(value instanceof Error ? value.message : "材料上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function addTextSource(event: FormEvent) {
    event.preventDefault();
    if (!detail || !textTitle.trim() || !textContent.trim()) return;
    setUploading(true);
    setError(null);
    try {
      await apiRequest(`/synthesis/sessions/${detail.id}/sources/text`, {
        method: "POST",
        body: JSON.stringify({ title: textTitle.trim(), content: textContent.trim() }),
      });
      setTextTitle("");
      setTextContent("");
      await openSession(detail.id);
      if (library) await refreshSessions(library.project_id);
      setNotice("补充文本已纳入文档关系分析。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "补充文本添加失败");
    } finally {
      setUploading(false);
    }
  }

  async function runWriting() {
    if (!detail) return;
    setRunning(true);
    setError(null);
    setNotice("后台任务已启动。离开本页面不会中断写作。各章节会独立保存。 ");
    try {
      const value = await apiRequest<SynthesisJob>(`/synthesis/sessions/${detail.id}/run`, {
        method: "POST",
      });
      setJob(value);
      const next = await apiRequest<SynthesisDetail>(`/synthesis/sessions/${detail.id}`);
      setDetail(next);
      if (library) await refreshSessions(library.project_id);
    } catch (value) {
      setRunning(false);
      setError(value instanceof Error ? value.message : "后台写作启动失败");
    }
  }

  function chooseSection(section: SynthesisSection) {
    setSelectedSectionId(section.id);
    setSectionDraft(section.draft_markdown);
    setTab("manuscript");
  }

  async function saveSection() {
    if (!detail || !selectedSection || !sectionDraft.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const value = await apiRequest<SynthesisSection>(
        `/synthesis/sessions/${detail.id}/sections/${selectedSection.id}`,
        { method: "PATCH", body: JSON.stringify({ draft_markdown: sectionDraft }) },
      );
      const next = await apiRequest<SynthesisDetail>(`/synthesis/sessions/${detail.id}`);
      setDetail(next);
      setSectionDraft(value.draft_markdown);
      setNotice("章节修订已保存，并同步重建全文。再次运行可让审查 Agent 基于新版本复核。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "章节保存失败");
    } finally {
      setSaving(false);
    }
  }

  const latestReview = detail?.reviews.at(-1);
  const relations = detail?.document_map.source_relations;
  const relationList = Array.isArray(relations) ? relations : [];

  return (
    <div className="synthesis-workspace">
      <header className="page-header synthesis-header">
        <div>
          <span className="eyebrow">CHAPTER-STATE SYNTHESIS</span>
          <h1>综述与论文写作工作台</h1>
          <p>文档理解 → 章节写作 → 证据核验 → 全文审查 → 定向迭代。每章独立持久化，长文不依赖一次上下文。</p>
        </div>
        <div className="synthesis-template-badge"><BookOpenText size={15} /> 16 篇去重论文 · 结构蒸馏模板</div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}
      {notice ? <div className="success-banner">{notice}</div> : null}

      <div className="synthesis-grid">
        <aside className="synthesis-left">
          <form className="synthesis-create" onSubmit={createSession}>
            <span className="eyebrow">NEW MANUSCRIPT</span>
            <label>论文标题（可稍后完善）<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：肿瘤免疫微环境研究进展" /></label>
            <label>研究主题与写作目标<textarea required value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="说明对象、范围、核心问题和希望形成的观点。" /></label>
            <div className="synthesis-form-row">
              <label>模型深度<select value={modelDepth} onChange={(event) => setModelDepth(event.target.value as typeof modelDepth)}><option value="quick">快速</option><option value="balanced">均衡</option><option value="deep">深入</option><option value="max">最大</option></select></label>
              <label>审查轮数<select value={reviewRounds} onChange={(event) => setReviewRounds(Number(event.target.value))}><option value={1}>1 轮</option><option value={2}>2 轮</option><option value={3}>3 轮</option></select></label>
            </div>
            <label>单章上下文<select value={contextTokens} onChange={(event) => setContextTokens(Number(event.target.value) as ContextTokens)}><option value={32768}>32K</option><option value={65536}>64K</option><option value={131072}>128K</option><option value={262144}>256K</option><option value={524288}>512K</option><option value={1000000}>1M</option></select><small className="synthesis-context-hint">上限受所选模型实际窗口约束；系统仍优先语义召回，不会为填满窗口而注水。</small></label>
            <label className="synthesis-check"><input type="checkbox" checked={includeNotes} onChange={(event) => setIncludeNotes(event.target.checked)} /><Layers3 size={13} />纳入全部工作台笔记</label>
            <label className="synthesis-check"><input type="checkbox" checked={onlineLiterature} onChange={(event) => setOnlineLiterature(event.target.checked)} /><Globe2 size={13} />联网补充可溯源文献</label>
            <button className="primary-action compact" disabled={creating || !topic.trim()}>{creating ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}创建写作项目</button>
          </form>

          <div className="synthesis-session-list">
            <span className="eyebrow">MANUSCRIPTS</span>
            {sessions.length ? sessions.map((item) => (
              <button key={item.id} className={detail?.id === item.id ? "active" : ""} onClick={() => void openSession(item.id)}>
                <span className={`synthesis-status-dot ${item.status}`} />
                <div><strong>{item.title}</strong><small>{STATUS_LABELS[item.status]} · {item.section_count} 章 · {item.source_count} 来源</small></div>
                <ChevronRight size={13} />
              </button>
            )) : <p>还没有写作项目。</p>}
          </div>

          {detail ? <div className="synthesis-chapter-tree">
            <span className="eyebrow">CHAPTER MEMORY</span>
            {detail.sections.length ? detail.sections.map((section) => (
              <button key={section.id} className={selectedSectionId === section.id ? "active" : ""} style={{ paddingLeft: `${12 + Math.max(0, section.level - 1) * 10}px` }} onClick={() => chooseSection(section)}>
                {["reviewed", "revised", "user_edited"].includes(section.status) ? <CheckCircle2 size={12} /> : <CircleDashed size={12} />}
                <span>{section.title}</span><small>v{section.revision}</small>
              </button>
            )) : <p>启动写作后，章节状态会在这里逐章出现。</p>}
          </div> : null}
        </aside>

        <main className="synthesis-main">
          {!detail ? <div className="synthesis-welcome">
            <div><Network size={28} /></div><span className="eyebrow">CONTEXT ENGINEERING</span><h2>不是一次性生成，而是可追踪的长文编排</h2>
            <p>系统先拆分并理解笔记与补充材料，建立文档关系、术语表和全局大纲；Writer 每次只读取当前章节最相关的来源与前文摘要；Reviewer 分章节与全文两级审查，仅重写受影响章节。</p>
            <div className="synthesis-flow"><span>Document Understanding</span><ChevronRight size={14} /><span>Writer</span><ChevronRight size={14} /><span>Reviewer</span><ChevronRight size={14} /><span>Selective Rewrite</span></div>
          </div> : <>
            <header className="synthesis-manuscript-header">
              <div><span>{STATUS_LABELS[detail.status]}</span><h2>{detail.title}</h2><p>{detail.topic}</p></div>
              <div className="synthesis-header-actions">
                <button onClick={() => void openSession(detail.id)}><RefreshCw size={13} />刷新</button>
                <button className="run" disabled={processing || running} onClick={() => void runWriting()}>{processing || running ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{detail.status === "completed" ? "再次审查迭代" : "启动写作"}</button>
              </div>
            </header>

            <div className="synthesis-progress">
              {[{ icon: Layers3, label: "文档理解", active: detail.sections.length > 0 }, { icon: PenLine, label: "章节写作", active: detail.sections.some((item) => Boolean(item.draft_markdown)) }, { icon: SearchCheck, label: "局部审查", active: detail.reviews.some((item) => item.scope === "section") }, { icon: GitBranch, label: "全文迭代", active: detail.reviews.some((item) => item.scope === "global") }].map(({ icon: Icon, label, active }) => <div key={label} className={active ? "active" : ""}><Icon size={14} /><span>{label}</span></div>)}
              {job && ["queued", "running", "retrying"].includes(job.status) ? <small><LoaderCircle className="spin" size={12} />后台运行中，页面可安全关闭</small> : null}
            </div>

            <nav className="synthesis-tabs">
              <button className={tab === "manuscript" ? "active" : ""} onClick={() => setTab("manuscript")}><FileText size={13} />正文</button>
              <button className={tab === "context" ? "active" : ""} onClick={() => setTab("context")}><Network size={13} />上下文记忆</button>
              <button className={tab === "review" ? "active" : ""} onClick={() => setTab("review")}><SearchCheck size={13} />审查记录</button>
              <div><a href={`${API_URL}/synthesis/sessions/${detail.id}/export?format=markdown`}><Download size={12} />Markdown</a><a href={`${API_URL}/synthesis/sessions/${detail.id}/export?format=docx`}><Download size={12} />DOCX</a></div>
            </nav>

            {tab === "manuscript" ? <section className="synthesis-document">
              {selectedSection ? <>
                <header><div><span>SECTION EDITOR · REVISION {selectedSection.revision}</span><h3>{selectedSection.title}</h3><p>{selectedSection.purpose}</p></div><button disabled={saving || !sectionDraft.trim()} onClick={() => void saveSection()}>{saving ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />}保存章节</button></header>
                <textarea className="synthesis-section-editor" value={sectionDraft} onChange={(event) => setSectionDraft(event.target.value)} />
                <footer><span>{selectedSection.source_ids.length} 个本地来源</span><span>{selectedSection.evidence_ids.length} 条文献证据</span><button onClick={() => { setSelectedSectionId(null); setSectionDraft(""); }}>返回全文</button></footer>
              </> : detail.manuscript_markdown ? <article className="brainstorm-markdown synthesis-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.manuscript_markdown}</ReactMarkdown></article> : <div className="synthesis-empty-document"><FileText size={27} /><h3>正文等待生成</h3><p>添加材料后启动写作。运行中每一章会立即保存，不必等待整篇结束。</p></div>}
            </section> : null}

            {tab === "context" ? <section className="synthesis-context-grid">
              <article><header><Network size={15} /><strong>全局记忆</strong></header><p>{detail.global_summary || "文档理解完成后生成跨章节摘要。"}</p><dl><div><dt>术语</dt><dd>{Object.keys(detail.glossary).length}</dd></div><div><dt>引用账本</dt><dd>{Object.keys(detail.citation_ledger).length}</dd></div><div><dt>图表</dt><dd>{detail.figure_manifest.length}</dd></div><div><dt>上下文</dt><dd>{Math.round(detail.max_context_tokens / 1024)}K</dd></div></dl></article>
              <article><header><GitBranch size={15} /><strong>文档关系</strong></header>{relationList.length ? relationList.map((item, index) => <div className="synthesis-relation" key={index}><span>{String((item as Record<string, unknown>).relation ?? "related")}</span><p>{String((item as Record<string, unknown>).description ?? "语义相关")}</p></div>) : <p>关系图将在文档理解阶段生成。</p>}</article>
              <article className="synthesis-source-card"><header><LibraryBig size={15} /><strong>来源快照</strong></header>{detail.sources.map((source) => <div key={source.id}><FileText size={12} /><span><strong>{source.filename}</strong><small>{source.source_type} · {source.module_count} 模块</small></span></div>)}</article>
              <article><header><PanelRightOpen size={15} /><strong>章节摘要链</strong></header>{detail.sections.filter((item) => item.section_summary).map((section) => <details key={section.id}><summary>{section.title}</summary><p>{section.section_summary}</p></details>)}</article>
              <article className="synthesis-figure-gallery"><header><GitBranch size={15} /><strong>科研图表</strong></header>{detail.figure_manifest.filter((item) => item.content_url && item.figure_id).length ? detail.figure_manifest.filter((item) => item.content_url && item.figure_id).map((item) => <a key={String(item.figure_id)} href={`${API_URL}${String(item.content_url)}`} target="_blank" rel="noreferrer"><Image unoptimized src={`${API_URL}${String(item.content_url)}`} alt={String(item.title ?? item.caption ?? "生成图表")} width={560} height={360} /><strong>{String(item.title ?? "生成图表")}</strong><small>{String(item.chart_type ?? "auto")} · {String(item.caption ?? "来源表格可追溯")}</small></a>) : <p>材料中存在可解析数据表时，Writer 会生成并登记图表。</p>}</article>
            </section> : null}

            {tab === "review" ? <section className="synthesis-review-list">
              {detail.reviews.length ? [...detail.reviews].reverse().map((review) => <article key={review.id} className={review.verdict === "approved" ? "approved" : "needs-work"}><header><div><Bot size={14} /><strong>{review.scope === "global" ? "全文审查" : "章节审查"} · 第 {review.round_number} 轮</strong></div><span>{review.verdict}</span></header><pre>{feedbackText(review.feedback)}</pre>{review.affected_section_keys.length ? <footer>影响章节：{review.affected_section_keys.join("、")}</footer> : null}</article>) : <div className="synthesis-empty-document"><SearchCheck size={27} /><h3>暂无审查记录</h3><p>Reviewer 会检查结构、逻辑、术语一致性、跨章重复和引用可追溯性。</p></div>}
            </section> : null}
          </>}
        </main>

        <aside className="synthesis-right">
          <section className="synthesis-agent-card"><header><Bot size={16} /><div><span className="eyebrow">AGENT STATUS</span><strong>协作轨迹</strong></div></header>{detail ? <><div className="synthesis-agent-row"><span>理解 Agent</span><strong>{detail.agent_runs.filter((item) => item.agent_name === "document_understanding").length} 次</strong></div><div className="synthesis-agent-row"><span>写作 Agent</span><strong>{detail.agent_runs.filter((item) => item.agent_name.includes("writer")).length} 次</strong></div><div className="synthesis-agent-row"><span>审查 Agent</span><strong>{detail.agent_runs.filter((item) => item.agent_name.includes("reviewer")).length} 次</strong></div><div className="synthesis-agent-row"><span>当前轮次</span><strong>{detail.current_round} / {detail.max_review_rounds}</strong></div></> : <p>选择写作项目后显示。</p>}</section>

          {detail ? <>
            <section className="synthesis-material-card"><header><FilePlus2 size={15} /><strong>补充额外资料</strong></header><form onSubmit={uploadSource}><input name="source-file" type="file" multiple accept=".pdf,.docx,.txt,.md,.markdown" /><button disabled={uploading}>{uploading ? <LoaderCircle className="spin" size={12} /> : <Upload size={12} />}批量上传并解析</button></form><details><summary>也可粘贴文本或实验说明</summary><form onSubmit={addTextSource}><input value={textTitle} onChange={(event) => setTextTitle(event.target.value)} placeholder="材料标题" /><textarea value={textContent} onChange={(event) => setTextContent(event.target.value)} placeholder="粘贴正文、实验结果或写作要求…" /><button disabled={uploading || !textTitle.trim() || !textContent.trim()}><Plus size={12} />纳入语境</button></form></details><small>支持一次选择多个 PDF、DOCX、TXT、Markdown；每个文件独立解析、按 SHA-256 内容去重，再按章节语义召回。</small></section>

            <section className="synthesis-metric-card"><header><Network size={15} /><strong>上下文完整性</strong></header><div><span>章节</span><strong>{detail.sections.length}</strong></div><div><span>来源</span><strong>{detail.sources.length}</strong></div><div><span>来源关系</span><strong>{relationList.length}</strong></div><div><span>证据引用</span><strong>{detail.sections.reduce((sum, item) => sum + item.evidence_ids.length, 0)}</strong></div><p>全文结构、术语和引用账本常驻；正文按章节召回，避免将整篇反复塞入模型。</p></section>

            <section className="synthesis-latest-review"><header><SearchCheck size={15} /><strong>最近审查</strong></header>{latestReview ? <><span className={latestReview.verdict === "approved" ? "approved" : "needs-work"}>{latestReview.verdict}</span><p>{feedbackText(latestReview.feedback).slice(0, 900)}</p></> : <p>完成章节草稿后开始审查。</p>}</section>
          </> : null}
        </aside>
      </div>
    </div>
  );
}
