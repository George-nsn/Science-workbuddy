"use client";

import { ArchiveRestore, Brain, DatabaseZap, LoaderCircle, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  apiRequest,
  type LibraryResponse,
  type ProjectFact,
  type RecycleBin,
} from "@/lib/api";

const ENTITY_LABELS: Record<RecycleBin["items"][number]["entity_type"], string> = {
  brainstorm_session: "头脑风暴会话",
  research_run: "研究运行",
  workbench_note: "工作台笔记",
  workbench_task: "工作台任务",
  workbench_attachment: "工作台附件",
};

export function MemoryGovernance() {
  const [projectId, setProjectId] = useState("");
  const [trash, setTrash] = useState<RecycleBin | null>(null);
  const [facts, setFacts] = useState<ProjectFact[]>([]);
  const [retention, setRetention] = useState(30);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function load(targetProjectId: string) {
    const [trashValue, factValue] = await Promise.all([
      apiRequest<RecycleBin>(`/memory/projects/${targetProjectId}/trash`, { cache: "no-store" }),
      apiRequest<{ facts: ProjectFact[] }>(`/memory/projects/${targetProjectId}/facts`, { cache: "no-store" }),
    ]);
    setTrash(trashValue);
    setRetention(trashValue.retention_days);
    setFacts(factValue.facts);
  }

  useEffect(() => {
    let active = true;
    apiRequest<LibraryResponse>("/library", { cache: "no-store" })
      .then(async (library) => {
        if (!active) return;
        setProjectId(library.project_id);
        await load(library.project_id);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "记忆治理加载失败");
      });
    return () => { active = false; };
  }, []);

  const categories = useMemo(() => new Set(facts.map((fact) => fact.category)).size, [facts]);

  async function saveRetention() {
    if (!projectId) return;
    setBusy("retention");
    try {
      await apiRequest(`/memory/projects/${projectId}/retention`, {
        method: "PUT",
        body: JSON.stringify({ trash_retention_days: retention }),
      });
      await load(projectId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保留期限保存失败");
    } finally {
      setBusy("");
    }
  }

  async function recycleAction(item: RecycleBin["items"][number], action: "restore" | "purge") {
    if (!projectId) return;
    setBusy(item.entity_id);
    try {
      const base = `/memory/projects/${projectId}/trash/${item.entity_type}/${item.entity_id}`;
      await apiRequest(action === "restore" ? `${base}/restore` : base, {
        method: action === "restore" ? "POST" : "DELETE",
      });
      await load(projectId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "回收站操作失败");
    } finally {
      setBusy("");
    }
  }

  async function setFactStatus(fact: ProjectFact, status: ProjectFact["status"]) {
    setBusy(fact.id);
    try {
      await apiRequest(`/memory/projects/${projectId}/facts/${fact.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await load(projectId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "事实状态更新失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="memory-page">
      <header className="page-header">
        <div><span className="eyebrow">DURABLE MEMORY GOVERNANCE</span><h1>记忆与回收站</h1><p>查看项目事实来源、修订事实状态，并控制软删除数据的保留期限。</p></div>
        <div className="boundary-badge"><Brain size={16} /> 可追踪 · 可恢复 · 可遗忘</div>
      </header>
      {error && <div className="error-banner">{error}</div>}

      <section className="memory-summary-grid">
        <article><Brain size={20} /><strong>{facts.length}</strong><span>条活跃项目事实</span></article>
        <article><DatabaseZap size={20} /><strong>{categories}</strong><span>个事实分类</span></article>
        <article><ArchiveRestore size={20} /><strong>{trash?.items.length ?? 0}</strong><span>项待恢复或清理</span></article>
      </section>

      <section className="memory-governance-grid">
        <article className="memory-card recycle-bin-card">
          <header><div><span className="eyebrow">RECYCLE BIN</span><h2>回收站</h2></div><label>保留 <input type="number" min={1} max={3650} value={retention} onChange={(event) => setRetention(Number(event.target.value))} /> 天 <button onClick={() => void saveRetention()} disabled={busy === "retention"}>{busy === "retention" ? <LoaderCircle className="spin" size={13} /> : "保存"}</button></label></header>
          <div className="recycle-list">
            {(trash?.items ?? []).map((item) => (
              <article key={`${item.entity_type}:${item.entity_id}`}>
                <div><span>{ENTITY_LABELS[item.entity_type]}</span><strong>{item.title}</strong><small>{new Date(item.deleted_at).toLocaleString("zh-CN")} 删除 · {new Date(item.purge_after).toLocaleDateString("zh-CN")} 后自动清理</small></div>
                <button onClick={() => void recycleAction(item, "restore")} disabled={!!busy}><RotateCcw size={13} />恢复</button>
                <button className="danger" onClick={() => void recycleAction(item, "purge")} disabled={!!busy}><Trash2 size={13} />永久删除</button>
              </article>
            ))}
            {!trash?.items.length && <p className="memory-empty">回收站为空。</p>}
          </div>
        </article>

        <article className="memory-card facts-card">
          <header><div><span className="eyebrow">PROJECT FACTS</span><h2>项目事实记忆</h2></div><span>{facts.length} 条</span></header>
          <div className="fact-list">
            {facts.map((fact) => (
              <article key={fact.id}>
                <header><span>{fact.category}</span><small>置信度 {Math.round(fact.confidence * 100)}% · 重要性 {Math.round(fact.importance * 100)}%</small></header>
                <p>{fact.statement}</p>
                <footer><span>{fact.source_type} · {String(fact.source_locator.field_path ?? fact.source_id)}</span><select value={fact.status} onChange={(event) => void setFactStatus(fact, event.target.value as ProjectFact["status"])} disabled={!!busy}><option value="active">有效</option><option value="superseded">已取代</option><option value="retracted">已撤回</option></select></footer>
              </article>
            ))}
            {!facts.length && <p className="memory-empty">完成一次头脑风暴后，结构化事实会显示在这里。</p>}
          </div>
        </article>
      </section>
    </div>
  );
}