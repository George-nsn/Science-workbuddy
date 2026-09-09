"use client";

import {
  Archive,
  Check,
  Database,
  FolderOpen,
  FileUp,
  Filter,
  Layers3,
  LoaderCircle,
  MessageSquareText,
  Network,
  Pencil,
  RefreshCw,
  RotateCcw,
  ScanSearch,
  Search,
  Sparkles,
  Tags,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  API_URL,
  apiRequest,
  type LibraryResponse,
  type LiteratureArchive,
  type RagCollection,
} from "@/lib/api";

export function LibraryWorkbench() {
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [archives, setArchives] = useState<LiteratureArchive[]>([]);
  const [activeArchiveId, setActiveArchiveId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [selectedPapers, setSelectedPapers] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const [enrichingPdfs, setEnrichingPdfs] = useState(false);
  const [enrichingMetadata, setEnrichingMetadata] = useState(false);
  const [uploadMode, setUploadMode] = useState<"single" | "multiple" | "folder">("single");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadReport, setUploadReport] = useState<{
    total: number;
    imported: number;
    failed: number;
    duplicates: number;
    paper_ids: string[];
    archive: LiteratureArchive | null;
    items: Array<{
      filename: string;
      relative_path: string;
      status: "imported" | "failed";
      error: string | null;
    }>;
  } | null>(null);
  const [fullTextPaper, setFullTextPaper] = useState<string | null>(null);
  const [editingPaper, setEditingPaper] = useState<string | null>(null);
  const [tagDraft, setTagDraft] = useState("");
  const [savingTags, setSavingTags] = useState(false);
  const [showCollectionDialog, setShowCollectionDialog] = useState(false);
  const [showArchiveDialog, setShowArchiveDialog] = useState(false);
  const [archiveName, setArchiveName] = useState("");
  const [archiving, setArchiving] = useState(false);
  const [deletingPapers, setDeletingPapers] = useState(false);
  const [collectionName, setCollectionName] = useState("");
  const [buildVector, setBuildVector] = useState(true);
  const [buildGraph, setBuildGraph] = useState(true);
  const [creatingCollection, setCreatingCollection] = useState(false);
  const [retryingCollection, setRetryingCollection] = useState<string | null>(null);
  const collectionNameRef = useRef<HTMLInputElement>(null);
  const archiveNameRef = useRef<HTMLInputElement>(null);
  const collectionTriggerRef = useRef<HTMLButtonElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const setFolderInput = useCallback((element: HTMLInputElement | null) => {
    folderInputRef.current = element;
    if (!element) return;
    element.setAttribute("webkitdirectory", "");
    element.setAttribute("directory", "");
  }, []);

  useEffect(() => {
    if (!showCollectionDialog) return;
    const previous = document.activeElement as HTMLElement | null;
    collectionNameRef.current?.focus();
    return () => previous?.focus();
  }, [showCollectionDialog]);

  useEffect(() => {
    if (!showArchiveDialog) return;
    const previous = document.activeElement as HTMLElement | null;
    archiveNameRef.current?.focus();
    return () => previous?.focus();
  }, [showArchiveDialog]);

  const loadLibrary = useCallback(async (
    search = query,
    tags = selectedTags,
    archiveId = activeArchiveId,
  ) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("q", search.trim());
      tags.forEach((tag) => params.append("tag_ids", tag));
      if (archiveId) params.set("archive_id", archiveId);
      const suffix = params.size ? `?${params}` : "";
      const value = await apiRequest<LibraryResponse>(`/library${suffix}`, {
        cache: "no-store",
      });
      setLibrary(value);
      const [collectionValue, archiveValue] = await Promise.all([
        apiRequest<{ collections: RagCollection[] }>(
          `/rag/collections?project_id=${value.project_id}`,
          { cache: "no-store" },
        ),
        apiRequest<{ archives: LiteratureArchive[] }>(
          `/library/archives?project_id=${value.project_id}`,
          { cache: "no-store" },
        ),
      ]);
      setCollections(collectionValue.collections);
      setArchives(archiveValue.archives);
    } catch (value) {
      setError(value instanceof Error ? value.message : "无法加载文献库");
    } finally {
      setLoading(false);
    }
  }, [activeArchiveId, query, selectedTags]);

  useEffect(() => {
    let active = true;
    Promise.all([
      apiRequest<LibraryResponse>("/library", { cache: "no-store" }),
    ])
      .then(async ([value]) => {
        if (!active) return;
        setLibrary(value);
        const [collectionValue, archiveValue] = await Promise.all([
          apiRequest<{ collections: RagCollection[] }>(
            `/rag/collections?project_id=${value.project_id}`,
            { cache: "no-store" },
          ),
          apiRequest<{ archives: LiteratureArchive[] }>(
            `/library/archives?project_id=${value.project_id}`,
            { cache: "no-store" },
          ),
        ]);
        if (active) {
          setCollections(collectionValue.collections);
          setArchives(archiveValue.archives);
        }
      })
      .catch((value: unknown) => {
        if (active) setError(value instanceof Error ? value.message : "无法加载文献库");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const projectId = library?.project_id;
    const hasActiveBuild = collections.some((collection) =>
      ["queued", "building"].includes(collection.vector_status),
    );
    if (!projectId || !hasActiveBuild) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void apiRequest<{ collections: RagCollection[] }>(
        `/rag/collections?project_id=${projectId}`,
        { cache: "no-store" },
      )
        .then((value) => {
          if (active) setCollections(value.collections);
        })
        .catch((value: unknown) => {
          if (active) setError(value instanceof Error ? value.message : "集合状态刷新失败");
        });
    }, 3000);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [collections, library?.project_id]);

  const visiblePaperIds = useMemo(
    () => new Set(library?.papers.map((paper) => paper.id) ?? []),
    [library],
  );
  const allVisibleSelected =
    visiblePaperIds.size > 0 && [...visiblePaperIds].every((id) => selectedPapers.has(id));

  async function searchLibrary(event: FormEvent) {
    event.preventDefault();
    setSelectedPapers(new Set());
    await loadLibrary(query, selectedTags, activeArchiveId);
  }

  async function toggleTagFilter(tagId: string) {
    const next = new Set(selectedTags);
    if (next.has(tagId)) next.delete(tagId);
    else next.add(tagId);
    setSelectedTags(next);
    setSelectedPapers(new Set());
    await loadLibrary(query, next, activeArchiveId);
  }

  function togglePaper(paperId: string) {
    setSelectedPapers((current) => {
      const next = new Set(current);
      if (next.has(paperId)) next.delete(paperId);
      else next.add(paperId);
      return next;
    });
  }

  function toggleVisiblePapers() {
    setSelectedPapers((current) => {
      const next = new Set(current);
      if (allVisibleSelected) visiblePaperIds.forEach((id) => next.delete(id));
      else visiblePaperIds.forEach((id) => next.add(id));
      return next;
    });
  }

  async function uploadPdf(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!library || !uploadFiles.length) return;
    const form = event.currentTarget;
    setUploading(true);
    setError(null);
    setNotice(null);
    setUploadReport(null);
    try {
      const data = new FormData();
      data.set("project_id", library.project_id);
      let endpoint = "/documents/upload-batch";
      if (uploadMode === "single") {
        endpoint = "/documents/upload";
        data.set("file", uploadFiles[0]);
        const title = new FormData(form).get("title");
        if (typeof title === "string" && title.trim()) data.set("title", title.trim());
      } else {
        uploadFiles.forEach((file) => {
          data.append("files", file, file.name);
          data.append("relative_paths", file.webkitRelativePath || file.name);
        });
      }
      const response = await fetch(`${API_URL}${endpoint}`, {
        method: "POST",
        body: data,
      });
      if (!response.ok) {
        let detail = `PDF 上传失败（${response.status}）`;
        try {
          const payload = (await response.json()) as { detail?: string };
          detail = payload.detail ?? detail;
        } catch {
          // Preserve the status fallback for proxy or non-JSON failures.
        }
        throw new Error(detail);
      }
      if (uploadMode !== "single") {
        const report = (await response.json()) as NonNullable<typeof uploadReport>;
        setUploadReport(report);
        setNotice(
          `文件夹处理完成：成功 ${report.imported}/${report.total}，失败 ${report.failed}` +
            (report.duplicates ? `，重复 ${report.duplicates}` : "") +
            (report.archive ? `；已建立“${report.archive.name}”。` : "。"),
        );
      } else {
        await response.json();
        setNotice("PDF 已解析入库，并完成自动标签整理。");
      }
      form.reset();
      setUploadFiles([]);
      await loadLibrary();
    } catch (value) {
      const message = value instanceof Error ? value.message : "PDF 上传失败";
      setError(
        message === "Failed to fetch"
          ? "无法连接本地 API。请确认后端仍在运行；若上传大文件后出现此提示，请查看 API 日志。"
          : message,
      );
    } finally {
      setUploading(false);
    }
  }

  function changeUploadMode(value: "single" | "multiple" | "folder") {
    setUploadMode(value);
    setUploadFiles([]);
    setUploadReport(null);
    setError(null);
  }

  function selectUploadFiles(files: FileList | null) {
    const selected = Array.from(files ?? []);
    const pdfs = selected.filter(
      (file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"),
    );
    setUploadFiles(uploadMode === "single" ? pdfs.slice(0, 1) : pdfs);
    setUploadReport(null);
    if (selected.length && !pdfs.length) {
      setNotice(null);
      setError(uploadMode === "folder" ? "所选文件夹内没有 PDF 文件。" : "所选内容中没有 PDF 文件。");
    }
    else if (pdfs.length !== selected.length) {
      setNotice(`已忽略 ${selected.length - pdfs.length} 个非 PDF 文件。`);
    }
  }

  async function importFullText(paperId: string) {
    if (!library) return;
    setFullTextPaper(paperId);
    setError(null);
    try {
      await apiRequest("/documents/import-europe-pmc", {
        method: "POST",
        body: JSON.stringify({ project_id: library.project_id, paper_id: paperId }),
      });
      setNotice("开放全文已导入，证据深度标签已自动刷新。");
      await loadLibrary();
    } catch (value) {
      setError(value instanceof Error ? value.message : "开放全文导入失败");
    } finally {
      setFullTextPaper(null);
    }
  }

  async function rebuildPdfEnrichment() {
    if (!library) return;
    setEnrichingPdfs(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiRequest<{
        processed: number;
        summaries_updated: number;
        tagged: number;
        failed: number;
      }>("/documents/enrich-pdfs", {
        method: "POST",
        body: JSON.stringify({ project_id: library.project_id }),
      });
      setNotice(
        `PDF 重建完成：处理 ${result.processed} 篇，更新摘要 ${result.summaries_updated} 篇，生成主题标签 ${result.tagged} 篇` +
          (result.failed ? `，失败 ${result.failed} 篇。` : "。"),
      );
      await loadLibrary();
    } catch (value) {
      setError(value instanceof Error ? value.message : "PDF 标签与摘要重建失败");
    } finally {
      setEnrichingPdfs(false);
    }
  }

  async function enrichScholarlyMetadata() {
    if (!library) return;
    setEnrichingMetadata(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiRequest<{
        processed: number;
        enriched: number;
        failed: number;
        references_linked: number;
      }>("/library/metadata/enrich", {
        method: "POST",
        body: JSON.stringify({ project_id: library.project_id, paper_ids: null }),
      });
      setNotice(
        `学术元数据补全完成：处理 ${result.processed} 篇，成功覆盖 ${result.enriched} 篇，新增 ${result.references_linked} 条本地引用边` +
          (result.failed ? `，${result.failed} 篇无可用 DOI/PMID 或来源请求失败。` : "。"),
      );
      await loadLibrary();
    } catch (value) {
      setError(value instanceof Error ? value.message : "学术元数据补全失败");
    } finally {
      setEnrichingMetadata(false);
    }
  }

  function beginTagEdit(paper: NonNullable<LibraryResponse>["papers"][number]) {
    setEditingPaper(paper.id);
    setTagDraft(paper.tags.map((tag) => tag.name).join(", "));
  }

  async function saveTags(paperId: string) {
    if (!library) return;
    const labels = tagDraft
      .split(/[,，]/)
      .map((value) => value.trim())
      .filter(Boolean);
    setSavingTags(true);
    setError(null);
    try {
      await apiRequest(`/library/papers/${paperId}/tags`, {
        method: "PUT",
        body: JSON.stringify({ project_id: library.project_id, labels }),
      });
      setEditingPaper(null);
      setNotice("标签已切换为手动整理模式。");
      await loadLibrary();
    } catch (value) {
      setError(value instanceof Error ? value.message : "标签保存失败");
    } finally {
      setSavingTags(false);
    }
  }

  async function resetTags(paperId: string) {
    if (!library) return;
    setError(null);
    try {
      await apiRequest(
        `/library/papers/${paperId}/tags/reset?project_id=${library.project_id}`,
        { method: "POST" },
      );
      setEditingPaper(null);
      setNotice("已恢复自动标签整理。");
      await loadLibrary();
    } catch (value) {
      setError(value instanceof Error ? value.message : "自动标签恢复失败");
    }
  }

  async function deleteTag(
    paper: NonNullable<LibraryResponse>["papers"][number],
    tagName: string,
  ) {
    if (!library) return;
    setSavingTags(true);
    setError(null);
    try {
      await apiRequest(`/library/papers/${paper.id}/tags`, {
        method: "PUT",
        body: JSON.stringify({
          project_id: library.project_id,
          labels: paper.tags
            .filter((tag) => tag.name !== tagName)
            .map((tag) => tag.name),
        }),
      });
      setNotice(`已删除标签“${tagName}”，该文献已进入手动整理模式。`);
      await loadLibrary();
    } catch (value) {
      setError(value instanceof Error ? value.message : "标签删除失败");
    } finally {
      setSavingTags(false);
    }
  }

  async function filterArchive(archiveId: string) {
    setActiveArchiveId(archiveId);
    setSelectedPapers(new Set());
    await loadLibrary(query, selectedTags, archiveId);
  }

  async function archiveSelectedPapers(event: FormEvent) {
    event.preventDefault();
    if (!library || !selectedPapers.size || !archiveName.trim()) return;
    setArchiving(true);
    setError(null);
    setNotice(null);
    try {
      const value = await apiRequest<LiteratureArchive>("/library/archives", {
        method: "POST",
        body: JSON.stringify({
          project_id: library.project_id,
          paper_ids: [...selectedPapers],
          name: archiveName.trim(),
        }),
      });
      setShowArchiveDialog(false);
      setArchiveName("");
      setSelectedPapers(new Set());
      setNotice(`已将所选文献归档到“${value.name}”，档案现有 ${value.paper_count} 篇。`);
      await loadLibrary(query, selectedTags, activeArchiveId);
    } catch (value) {
      setError(value instanceof Error ? value.message : "文献归档失败");
    } finally {
      setArchiving(false);
    }
  }

  async function deleteSelectedPapers() {
    if (!library || !selectedPapers.size) return;
    const count = selectedPapers.size;
    if (!window.confirm(`确定从当前项目删除所选 ${count} 篇文献吗？此操作不可撤销。`)) return;
    setDeletingPapers(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiRequest<{
        removed_from_project: number;
        deleted_globally: number;
        collections_updated: number;
        collections_deleted: number;
      }>("/library/papers/bulk-delete", {
        method: "POST",
        body: JSON.stringify({
          project_id: library.project_id,
          paper_ids: [...selectedPapers],
        }),
      });
      setSelectedPapers(new Set());
      setNotice(
        `已从项目删除 ${result.removed_from_project} 篇文献` +
          (result.deleted_globally ? `，并清理 ${result.deleted_globally} 份无引用原始数据` : "") +
          (result.collections_deleted ? `；移除 ${result.collections_deleted} 个空 RAG 集合` : "") +
          "。",
      );
      await loadLibrary(query, selectedTags, activeArchiveId);
    } catch (value) {
      setError(value instanceof Error ? value.message : "文献删除失败");
    } finally {
      setDeletingPapers(false);
    }
  }

  async function createCollection(event: FormEvent) {
    event.preventDefault();
    if (!library || selectedPapers.size === 0) return;
    setCreatingCollection(true);
    setError(null);
    try {
      const collection = await apiRequest<RagCollection>("/rag/collections", {
        method: "POST",
        body: JSON.stringify({
          project_id: library.project_id,
          paper_ids: [...selectedPapers],
          name: collectionName.trim() || null,
          build_vector: buildVector,
          build_graph: buildGraph,
        }),
      });
      setCollections((current) => [collection, ...current]);
      setSelectedPapers(new Set());
      setCollectionName("");
      setShowCollectionDialog(false);
      setNotice(
        `${collection.name} 已创建：${collection.paper_count} 篇文献；向量 ${collection.vector_status}；图谱 ${collection.graph_status}${collection.warning ? `。${collection.warning}` : ""}`,
      );
    } catch (value) {
      setError(value instanceof Error ? value.message : "RAG 集合创建失败");
    } finally {
      setCreatingCollection(false);
    }
  }

  async function retryVector(collectionId: string) {
    setRetryingCollection(collectionId);
    setError(null);
    try {
      const updated = await apiRequest<RagCollection>(
        `/rag/collections/${collectionId}/vector`,
        { method: "POST" },
      );
      setCollections((current) =>
        current.map((collection) =>
          collection.id === updated.id ? updated : collection,
        ),
      );
      setNotice(updated.warning ?? `${updated.name} 的向量任务已重新提交。`);
    } catch (value) {
      setError(value instanceof Error ? value.message : "向量任务重试失败");
    } finally {
      setRetryingCollection(null);
    }
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLFormElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setShowCollectionDialog(false);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
      ),
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <>
      <header className="page-header library-page-header">
        <div>
          <span className="eyebrow">LOCAL LITERATURE LIBRARY</span>
          <h1>{library?.project_name ?? "个人文献库"}</h1>
          <p>自动标签整理，也可手动修改；筛选并多选文献生成独立向量知识库与知识图谱。</p>
        </div>
        <div className="library-header-actions">
          <Link className="brainstorm-link" href="/brainstorm"><MessageSquareText size={16} />课题头脑风暴</Link>
          <form className="library-search" onSubmit={searchLibrary}>
            <Search size={17} />
            <input
              aria-label="搜索文献"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标题、摘要、PMID、DOI…"
              value={query}
            />
            <button disabled={loading} type="submit">
              {loading ? <LoaderCircle className="spin" size={15} /> : "搜索"}
            </button>
          </form>
        </div>
      </header>

      <section className="tag-filter-bar">
        <div><Filter size={15} /><strong>标签筛选</strong></div>
        <div className="tag-filter-list">
          {library?.available_tags.map((tag) => (
            <button
              aria-label={`${tag.name}，${tag.paper_count} 篇文献`}
              aria-pressed={selectedTags.has(tag.id)}
              className={selectedTags.has(tag.id) ? "selected" : ""}
              key={tag.id}
              onClick={() => toggleTagFilter(tag.id)}
            >
              {tag.name}<span>{tag.paper_count}</span>
            </button>
          ))}
          {!library?.available_tags.length && <span className="tag-empty">暂无标签</span>}
        </div>
        <button className="icon-action" onClick={() => loadLibrary()} title="刷新">
          <RefreshCw size={15} />
        </button>
      </section>

      {error && <div aria-live="assertive" className="error-banner" role="alert">{error}</div>}
      {notice && <div aria-live="polite" className="success-banner" role="status">{notice}</div>}

      <section className="upload-panel batch-upload-panel">
        <div><FileUp size={22} /><div><strong>上传个人 PDF</strong><span>支持单个 PDF、多个 PDF 或整个文件夹；批量导入会自动建立“新增档案”。</span></div></div>
        <button className="pdf-enrichment-action" disabled={enrichingPdfs || uploading} onClick={rebuildPdfEnrichment} type="button">
          {enrichingPdfs ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
          {enrichingPdfs ? "正在重建" : "重建标签与摘要"}
        </button>
        <button className="pdf-enrichment-action" disabled={enrichingMetadata || uploading} onClick={enrichScholarlyMetadata} type="button">
          {enrichingMetadata ? <LoaderCircle className="spin" size={14} /> : <Network size={14} />}
          {enrichingMetadata ? "正在补全" : "补全 OA / 撤稿 / 引用元数据"}
        </button>
        <form onSubmit={uploadPdf}>
          <div className="upload-mode-switch">
            <button className={uploadMode === "single" ? "active" : ""} onClick={() => changeUploadMode("single")} type="button"><FileUp size={13} />单个 PDF</button>
            <button className={uploadMode === "multiple" ? "active" : ""} onClick={() => changeUploadMode("multiple")} type="button"><Layers3 size={13} />多个 PDF</button>
            <button className={uploadMode === "folder" ? "active" : ""} onClick={() => changeUploadMode("folder")} type="button"><FolderOpen size={13} />整个文件夹</button>
          </div>
          {uploadMode === "single" ? (
            <>
              <input aria-label="论文标题" name="title" placeholder="论文标题（可选）" />
              <input accept="application/pdf,.pdf" aria-label="选择单个 PDF" key="single-pdf" onChange={(event) => selectUploadFiles(event.target.files)} required type="file" />
            </>
          ) : uploadMode === "multiple" ? (
            <input accept="application/pdf,.pdf" aria-label="选择多个 PDF" key="multiple-pdf" multiple onChange={(event) => selectUploadFiles(event.target.files)} required type="file" />
          ) : (
            <input accept="application/pdf,.pdf" aria-label="选择 PDF 文件夹" key="folder-pdf" multiple onChange={(event) => selectUploadFiles(event.target.files)} ref={setFolderInput} required type="file" />
          )}
          <button disabled={uploading || !uploadFiles.length} type="submit">
            {uploading ? <LoaderCircle className="spin" size={15} /> : uploadMode === "folder" ? <FolderOpen size={15} /> : <FileUp size={15} />}
            {uploading ? "正在解析 PDF…" : uploadMode === "folder" ? `上传文件夹（${uploadFiles.length}）` : uploadMode === "multiple" ? `上传 ${uploadFiles.length} 个 PDF` : "上传并分块"}
          </button>
        </form>
        {uploadMode !== "single" && uploadFiles.length > 0 && !uploading && (
          <div className="upload-selection-summary">{uploadMode === "folder" ? "已递归读取文件夹" : "已选择"} {uploadFiles.length} 个 PDF</div>
        )}
        {uploadReport && uploadReport.failed > 0 && (
          <details className="upload-report"><summary>查看 {uploadReport.failed} 个失败文件</summary><ul>{uploadReport.items.filter((item) => item.status === "failed").map((item) => <li key={item.relative_path}><strong>{item.relative_path}</strong><span>{item.error}</span></li>)}</ul></details>
        )}
      </section>

      <section className="library-summary">
        <article><Database size={20} /><div><strong>{library?.papers.length ?? 0}</strong><span>当前筛选结果</span></div></article>
        <article><Tags size={20} /><div><strong>{library?.available_tags.length ?? 0}</strong><span>可用标签</span></div></article>
        <article><Layers3 size={20} /><div><strong>{collections.length}</strong><span>RAG 集合</span></div></article>
      </section>

      {!!collections.length && (
        <section className="collection-strip">
          <span>已生成</span>
          {collections.slice(0, 6).map((collection) => (
            <div key={collection.id}>
              <strong>{collection.name}</strong>
              <small>
                {collection.paper_count} 篇 · 向量 {collectionStatus(collection.vector_status)} · 图谱 {collectionStatus(collection.graph_status)}
              </small>
              {["pending", "failed"].includes(collection.vector_status) && (
                <button
                  disabled={retryingCollection === collection.id}
                  onClick={() => retryVector(collection.id)}
                >
                  {retryingCollection === collection.id ? "提交中" : "重试向量"}
                </button>
              )}
            </div>
          ))}
        </section>
      )}

      {!!archives.length && (
        <section className="archive-filter-strip">
          <span><Archive size={14} />文献档案</span>
          {archives.filter((archive) => archive.origin === "upload_batch").map((archive) => (
            <button className={`${activeArchiveId === archive.id ? "active " : ""}new-archive`} key={archive.id} onClick={() => filterArchive(archive.id)}>
              <Sparkles size={11} />{archive.name}<small>{archive.paper_count}</small>
            </button>
          ))}
          <button className={!activeArchiveId ? "active" : ""} onClick={() => filterArchive("")}>全部文献</button>
          {archives.filter((archive) => archive.origin !== "upload_batch").map((archive) => (
            <button className={activeArchiveId === archive.id ? "active" : ""} key={archive.id} onClick={() => filterArchive(archive.id)}>
              {archive.name}<small>{archive.paper_count}</small>
            </button>
          ))}
        </section>
      )}

      <section className="results-section">
        <div className="results-heading library-results-heading">
          <div>
            <span className="eyebrow">IMPORTED PAPERS</span>
            <h2>文献记录</h2>
          </div>
          <label className="select-all">
            <input checked={allVisibleSelected} onChange={toggleVisiblePapers} type="checkbox" />
            选择当前结果
          </label>
        </div>
        {loading && !library ? (
          <div className="empty-state"><LoaderCircle className="spin" size={22} /> 正在加载文献库</div>
        ) : library?.papers.length ? (
          <div className="library-grid">
            {library.papers.map((paper) => (
              <article
                data-selected={selectedPapers.has(paper.id)}
                className={`library-card selectable-card ${selectedPapers.has(paper.id) ? "selected" : ""}`}
                key={paper.id}
              >
                <label className="paper-selector">
                  <input
                    checked={selectedPapers.has(paper.id)}
                    onChange={() => togglePaper(paper.id)}
                    type="checkbox"
                  />
                  <span>{selectedPapers.has(paper.id) ? <Check size={13} /> : null}</span>
                </label>
                <div className="paper-meta">
                  <span>{paper.publication_year ?? "年份未知"}</span>
                  <span>{paper.journal ?? "期刊未知"}</span>
                  <span
                    className="paper-journal-metric-pill"
                    title={paper.journal_metric?.note ?? "尚未补全开放期刊指标"}
                  >
                    2年影响力 {formatOpenImpact(paper.journal_metric?.two_year_mean_citedness)}
                  </span>
                  <span
                    className="paper-journal-metric-pill quartile"
                    title={quartileTitle(paper.journal_metric)}
                  >
                    {formatOpenQuartile(paper.journal_metric?.open_quartile)}
                  </span>
                  {paper.is_retracted && <span className="paper-warning-pill">已撤稿</span>}
                  {paper.is_open_access && <span className="paper-oa-pill">开放获取</span>}
                  {journalQualityLabels(paper.quality_signals).map((label) => (
                    <span className="paper-oa-pill" key={label}>{label}</span>
                  ))}
                  {paper.citation_count !== null && <span>被引 {paper.citation_count}</span>}
                </div>
                <h3>{paper.title}</h3>
                <div className="paper-tags">
                  {paper.tags.map((tag) => (
                    <span
                      className={tag.origin}
                      key={tag.id}
                      title={tag.origin === "manual" ? "手动标签" : tag.origin === "brainstorm" ? "会话标签" : "自动标签"}
                    >
                      <span aria-hidden="true">{tag.origin === "manual" ? "✎" : tag.origin === "brainstorm" ? "✦" : "◆"}</span> {tag.name}
                      <span className="sr-only">（{tag.origin === "manual" ? "手动" : tag.origin === "brainstorm" ? "会话" : "自动"}标签）</span>
                      <button
                        aria-label={`删除标签 ${tag.name}`}
                        disabled={savingTags}
                        onClick={() => deleteTag(paper, tag.name)}
                        title="删除标签并切换为手动整理"
                      ><X size={9} /></button>
                    </span>
                  ))}
                  <button onClick={() => beginTagEdit(paper)} title="编辑标签">
                    <Pencil size={12} />
                  </button>
                </div>
                {editingPaper === paper.id && (
                  <div className="tag-editor">
                    <input
                      autoFocus
                      onChange={(event) => setTagDraft(event.target.value)}
                      placeholder="以逗号分隔标签"
                      value={tagDraft}
                    />
                    <button disabled={savingTags} onClick={() => saveTags(paper.id)}>
                      {savingTags ? <LoaderCircle className="spin" size={13} /> : <Check size={13} />}保存
                    </button>
                    <button onClick={() => resetTags(paper.id)}><RotateCcw size={13} />自动</button>
                    <button onClick={() => setEditingPaper(null)}><X size={13} /></button>
                  </div>
                )}
                {paper.abstract ? (
                  <div className="paper-summary">
                    <span>{paper.abstract_source === "local_extractive_pdf" ? "PDF 本地抽取摘要" : "文献摘要"}</span>
                    <p>{paper.abstract}</p>
                  </div>
                ) : (
                  <p>无摘要；该记录当前不能用于段落级证据回答。</p>
                )}
                <footer className="library-card-footer">
                  <div className="paper-identifiers">
                    {paper.pmid && <span>PMID {paper.pmid}</span>}
                    {paper.pmcid && <span>{paper.pmcid}</span>}
                    {paper.doi && <span>DOI {paper.doi}</span>}
                  </div>
                  {paper.pmcid && (
                    <button disabled={fullTextPaper === paper.id} onClick={() => importFullText(paper.id)}>
                      {fullTextPaper === paper.id ? <LoaderCircle className="spin" size={14} /> : <ScanSearch size={14} />}
                      导入开放全文
                    </button>
                  )}
                </footer>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-state">没有符合当前搜索与标签筛选的文献。</div>
        )}
      </section>

      {selectedPapers.size > 0 && (
        <div className="selection-toolbar">
          <div><strong>{selectedPapers.size}</strong><span>篇文献已选择</span></div>
          <button onClick={() => setSelectedPapers(new Set())}>取消选择</button>
          <button disabled={archiving || deletingPapers} onClick={() => setShowArchiveDialog(true)}><Archive size={15} />归档</button>
          <button className="danger" disabled={archiving || deletingPapers} onClick={deleteSelectedPapers}>{deletingPapers ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}删除</button>
          <button className="primary" onClick={() => setShowCollectionDialog(true)} ref={collectionTriggerRef}>
            <Sparkles size={16} />生成 RAG 内容
          </button>
        </div>
      )}

      {showArchiveDialog && (
        <div className="dialog-backdrop" onMouseDown={() => setShowArchiveDialog(false)} role="presentation">
          <form aria-labelledby="archive-dialog-title" aria-modal="true" className="rag-dialog archive-dialog" onKeyDown={handleDialogKeyDown} onMouseDown={(event) => event.stopPropagation()} onSubmit={archiveSelectedPapers} role="dialog">
            <header><div><span className="eyebrow">CREATE LITERATURE ARCHIVE</span><h2 id="archive-dialog-title">命名文献档案</h2></div><button aria-label="关闭对话框" onClick={() => setShowArchiveDialog(false)} type="button"><X size={18} /></button></header>
            <p>将所选 {selectedPapers.size} 篇文献归入一个命名档案。同名档案已存在时会追加，不会复制文献。</p>
            <label>档案名称<input maxLength={200} onChange={(event) => setArchiveName(event.target.value)} placeholder="例如：噬菌体抗防御系统" ref={archiveNameRef} required value={archiveName} /></label>
            <footer><button onClick={() => setShowArchiveDialog(false)} type="button">取消</button><button className="primary" disabled={archiving || !archiveName.trim()} type="submit">{archiving ? <LoaderCircle className="spin" size={15} /> : <Archive size={15} />}{archiving ? "归档中" : "确认归档"}</button></footer>
          </form>
        </div>
      )}

      {showCollectionDialog && (
        <div className="dialog-backdrop" onMouseDown={() => setShowCollectionDialog(false)} role="presentation">
          <form aria-labelledby="rag-dialog-title" aria-modal="true" className="rag-dialog" onKeyDown={handleDialogKeyDown} onMouseDown={(event) => event.stopPropagation()} onSubmit={createCollection} role="dialog">
            <header><div><span className="eyebrow">CREATE RAG COLLECTION</span><h2 id="rag-dialog-title">生成知识集合</h2></div><button aria-label="关闭对话框" onClick={() => setShowCollectionDialog(false)} type="button"><X size={18} /></button></header>
            <p>将使用已选择的 {selectedPapers.size} 篇文献。名称可选，留空会自动生成。</p>
            <label>集合名称（可选）<input onChange={(event) => setCollectionName(event.target.value)} placeholder="例如：BRAF 预后研究" ref={collectionNameRef} value={collectionName} /></label>
            <div className="rag-options">
              <label><input checked={buildVector} onChange={(event) => setBuildVector(event.target.checked)} type="checkbox" /><BrainCircuitIcon />向量知识库</label>
              <label><input checked={buildGraph} onChange={(event) => setBuildGraph(event.target.checked)} type="checkbox" /><Network />知识图谱</label>
            </div>
            <footer><button onClick={() => setShowCollectionDialog(false)} type="button">取消</button><button className="primary" disabled={creatingCollection || (!buildVector && !buildGraph)} type="submit">{creatingCollection ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}开始生成</button></footer>
          </form>
        </div>
      )}
    </>
  );
}

function BrainCircuitIcon() {
  return <Layers3 size={18} />;
}

function collectionStatus(status: string) {
  return {
    not_requested: "未选择",
    pending: "待处理",
    queued: "排队中",
    building: "构建中",
    ready: "已就绪",
    failed: "失败",
  }[status] ?? status;
}

function journalQualityLabels(qualitySignals: Record<string, unknown>) {
  const value = qualitySignals.journal;
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const journal = value as Record<string, unknown>;
  const labels: string[] = [];
  if (journal.is_in_doaj === true) labels.push("DOAJ 收录");
  if (journal.is_core === true) labels.push("OpenAlex 核心来源");
  return labels;
}

function formatOpenImpact(value: number | null | undefined) {
  return value === null || value === undefined ? "未知" : value.toFixed(2);
}

function formatOpenQuartile(value: string | null | undefined) {
  return value?.replace(/^OA-/i, "") ?? "Q未知";
}

function quartileTitle(metric: LibraryResponse["papers"][number]["journal_metric"]) {
  if (!metric) return "尚未补全开放分区";
  const topic = metric.quartile_basis.topic;
  const scope = topic ? `OpenAlex 主题：${String(topic)}` : "OpenAlex 全部期刊";
  const percentile = metric.percentile === null
    ? "百分位未知"
    : `百分位 ${(metric.percentile * 100).toFixed(1)}%`;
  return `${scope} · ${percentile}。不是 JCR/中科院/SJR 分区。`;
}
