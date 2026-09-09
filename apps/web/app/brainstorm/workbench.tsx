"use client";

import {
  BookOpenCheck,
  BrainCircuit,
  CheckCircle2,
  Compass,
  FileUp,
  FlaskConical,
  Microscope,
  Bot,
  Lightbulb,
  ListTodo,
  LoaderCircle,
  MessageSquareText,
  MoreHorizontal,
  Pencil,
  Gauge,
  History,
  RefreshCw,
  RotateCcw,
  Send,
  Settings2,
  ShieldAlert,
  Sparkles,
  Trash2,
  UserRound,
  WandSparkles,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { MermaidDiagram } from "@/components/mermaid-diagram";
import {
  API_URL,
  apiRequest,
  type BrainstormBackgroundJob,
  type BrainstormDetail,
  type BrainstormSession,
  type BrainstormPlanSnapshot,
  type BrainstormTaskDispatch,
  type BrainstormTurn,
  type LibraryResponse,
  type PrefetchJob,
  type RagCollection,
} from "@/lib/api";

type ModelStatus = { configured: boolean; provider: string | null; model: string | null };

type CoordinatorPayload = {
  title?: string;
  proposal_background?: string;
  scientific_question_and_hypothesis?: string;
  scientific_significance?: string;
  technical_route_summary?: string;
  novelty_and_limitations?: string;
  response_markdown?: string;
  technical_route_mermaid?: string | null;
  confirmation_questions?: string[];
  safety_flags?: string[];
  revised_document_markdown?: string | null;
  change_log?: Array<{ section?: string; change?: string; reason?: string }>;
};

type ExperimentPayload = {
  design_summary?: string;
  materials?: Array<{
    name?: string;
    category?: string;
    specification?: string;
    manufacturer?: string | null;
    catalog_model?: string | null;
    verification_status?: string;
    verification_note?: string;
  }>;
};

type MethodPayload = {
  method_overview?: string;
  modules?: Array<{
    title?: string;
    objective?: string;
    principle?: string;
    staged_procedure?: string[];
    controls?: string[];
    quality_control?: string[];
    acceptance_and_decision_criteria?: string[];
    failure_modes_and_troubleshooting?: string[];
    parameter_gaps?: string[];
  }>;
  reproducibility_checklist?: string[];
};

type GenerationQualityPayload = {
  mode?: "model_complete" | "fallback_assisted";
  fallback_agents?: string[];
  draft_loops?: Record<string, {
    refinement_rounds?: number;
    review_count?: number;
    stop_reason?: string;
    draft_characters?: number;
  }>;
};

function formatMarkdownContent(raw?: string | null): string {
  if (!raw) return "";
  let text = String(raw).trim();

  // If text contains a JSON block with response_markdown
  const fullJsonMatch = text.match(/(\{[\s\S]*?"response_markdown"[\s\S]*?\})/);
  if (fullJsonMatch && fullJsonMatch.index !== undefined) {
    try {
      const parsed = JSON.parse(fullJsonMatch[1]);
      if (parsed && typeof parsed === "object" && typeof parsed.response_markdown === "string") {
        const rest = text.slice(fullJsonMatch.index + fullJsonMatch[1].length).trim();
        text = rest ? `${parsed.response_markdown.trim()}\n\n${rest}` : parsed.response_markdown.trim();
      }
    } catch {
      // If full parse failed, try regex match for response_markdown value
      const valMatch = text.match(/"response_markdown"\s*:\s*"([\s\S]*?)(?:"\s*,\s*"[a-zA-Z0-9_]+"\s*:|"\s*\})/);
      if (valMatch && valMatch[1]) {
        try {
          const unescaped = JSON.parse(`"${valMatch[1]}"`);
          const rest = text.slice(valMatch.index! + valMatch[0].length).replace(/^[\s"}\]]+/, "").trim();
          text = rest ? `${unescaped}\n\n${rest}` : unescaped;
        } catch {
          // ignore
        }
      }
    }
  } else if (text.startsWith("```json") || text.startsWith("```")) {
    const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[1].trim());
        if (parsed && typeof parsed === "object" && typeof parsed.response_markdown === "string") {
          text = parsed.response_markdown.trim();
        }
      } catch {
        // ignore
      }
    }
  }

  // Convert literal escaped newlines to real newlines
  if (text.includes("\\n")) {
    text = text.replace(/\\n/g, "\n");
  }
  if (text.includes('\\"')) {
    text = text.replace(/\\"/g, '"');
  }
  return text;
}

function formatUserMessageContent(content: string): string {
  if (!content) return "";
  if (content.startsWith("Generate the final technical route and experimental proposal")) {
    try {
      const match = content.match(/\{[\s\S]*\}/);
      if (match) {
        const parsed = JSON.parse(match[0]);
        const dirTitle = parsed?.direction?.title;
        if (dirTitle) {
          return `一键生成技术路线与实验方案 · 目标方向：${dirTitle}`;
        }
      }
    } catch {
      // ignore
    }
    return "一键生成技术路线与实验方案";
  }
  if (content.startsWith("选择方向：") && content.includes("偏好画像：")) {
    const dirMatch = content.match(/选择方向：([^\s]+)/);
    return `已选研究方向：${dirMatch ? dirMatch[1] : "定制方向"}（实验偏好已绑定）`;
  }
  return content;
}

function formatCoordinatorMarkdown(content: string, payload: unknown): string {
  const coordinatorPayload =
    payload && typeof payload === "object"
      ? ((payload as Record<string, unknown>).coordinator as CoordinatorPayload | undefined)
      : undefined;
  const response = formatMarkdownContent(coordinatorPayload?.response_markdown ?? content);
  const chapters = [
    ["## 一、选题背景、立项依据与国内外研究进展", coordinatorPayload?.proposal_background],
    [
      "## 二、核心大科学问题与子科学问题假说链条",
      coordinatorPayload?.scientific_question_and_hypothesis,
    ],
    ["## 三、理论科学意义与应用价值", coordinatorPayload?.scientific_significance],
    ["## 四、总体实验架构与技术路线", coordinatorPayload?.technical_route_summary],
    [
      "## 五、课题创新性、局限性审判与待确认决策",
      coordinatorPayload?.novelty_and_limitations,
    ],
  ] as const;
  const populated = chapters.filter(([, value]) => value?.trim());
  if (!populated.length || chapters.some(([heading]) => response.includes(heading))) {
    return response;
  }
  const structured = [
    `# ${coordinatorPayload?.title?.trim() || "研究方案"}`,
    ...populated.flatMap(([heading, value]) => [heading, formatMarkdownContent(value)]),
  ].join("\n\n");
  const includesResponse = populated.some(([, value]) => response.includes(String(value).trim()));
  return response && !includesResponse
    ? `${structured}\n\n### 协调补充说明\n\n${response}`
    : structured;
}

export function BrainstormWorkbench() {
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [sessions, setSessions] = useState<BrainstormSession[]>([]);
  const [detail, setDetail] = useState<BrainstormDetail | null>(null);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [mode, setMode] = useState<"exploration" | "refinement">("exploration");
  const [workflow, setWorkflow] = useState<"classic" | "plan">("plan");
  const [title, setTitle] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [allowPubmed, setAllowPubmed] = useState(true);
  const [allowModelProcessing, setAllowModelProcessing] = useState(false);
  const [allowWebSearch, setAllowWebSearch] = useState(false);
  const [modelDepth, setModelDepth] = useState<"quick" | "balanced" | "deep" | "max">("max");
  const [maxContextTokens, setMaxContextTokens] = useState<32768 | 65536 | 131072 | 262144 | 524288 | 1000000>(1000000);
  const [agentBackground, setAgentBackground] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [planSeed, setPlanSeed] = useState("");
  const [selectedDirection, setSelectedDirection] = useState("");
  const [objectiveType, setObjectiveType] = useState("");
  const [modelSystem, setModelSystem] = useState("");
  const [budgetLevel, setBudgetLevel] = useState<"" | "low" | "medium" | "high">("");
  const [timelineWeeks, setTimelineWeeks] = useState("");
  const [sampleAvailability, setSampleAvailability] = useState("");
  const [riskTolerance, setRiskTolerance] = useState<"" | "conservative" | "balanced" | "aggressive">("");
  const [preferenceFreeText, setPreferenceFreeText] = useState("");
  const [preferenceAnswers, setPreferenceAnswers] = useState<Record<string, string | string[]>>({});
  const [planBusy, setPlanBusy] = useState("");
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [dispatchingMessageId, setDispatchingMessageId] = useState<string | null>(null);
  const [restoringVersionId, setRestoringVersionId] = useState<string | null>(null);
  const [regeneratingMessageId, setRegeneratingMessageId] = useState<string | null>(null);
  const [sessionMenuId, setSessionMenuId] = useState<string | null>(null);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renamingTitle, setRenamingTitle] = useState("");
  const [sessionActionBusy, setSessionActionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      apiRequest<LibraryResponse>("/library", { cache: "no-store" }),
      apiRequest<ModelStatus>("/research/model-status", { cache: "no-store" }),
    ])
      .then(async ([libraryValue, modelValue]) => {
        if (!active) return;
        setLibrary(libraryValue);
        setModelStatus(modelValue);
        const [collectionValue, sessionValue] = await Promise.all([
          apiRequest<{ collections: RagCollection[] }>(
            `/rag/collections?project_id=${libraryValue.project_id}`,
            { cache: "no-store" },
          ),
          apiRequest<{ sessions: BrainstormSession[] }>(
            `/brainstorm/sessions?project_id=${libraryValue.project_id}`,
            { cache: "no-store" },
          ),
        ]);
        if (active) {
          setCollections(collectionValue.collections);
          setSessions(sessionValue.sessions);
          if (sessionValue.sessions.length > 0) {
            void openSession(sessionValue.sessions[0].id);
          }
        }
      })
      .catch((value: unknown) => {
        if (active) setError(value instanceof Error ? value.message : "头脑风暴工作台加载失败");
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const latestAssistant = useMemo(
    () => [...(detail?.messages ?? [])].reverse().find((item) => item.role === "assistant"),
    [detail],
  );
  const latestPayload = latestAssistant?.payload ?? {};
  const coordinator = (latestPayload.coordinator ?? {}) as CoordinatorPayload;
  const experiment = (latestPayload.experiment_agent ?? {}) as ExperimentPayload;
  const method = (latestPayload.method_agent ?? {}) as MethodPayload;
  const generationQuality = (latestPayload.generation_quality ?? {}) as GenerationQualityPayload;
  const hasOriginal = detail?.versions.some((version) => version.kind === "original") ?? false;
  const originalVersion = detail?.versions.find((version) => version.kind === "original");
  const latestVersion = detail?.versions.at(-1);
  const sessionProcessing = detail?.session.status === "processing";
  const processingSessionId = sessionProcessing ? detail.session.id : null;
  const projectId = library?.project_id;

  useEffect(() => {
    if (!processingSessionId) return;
    let active = true;
    const timer = window.setInterval(() => {
      void apiRequest<BrainstormDetail>(`/brainstorm/sessions/${processingSessionId}`, {
        cache: "no-store",
      }).then((value) => {
        if (!active) return;
        setDetail(value);
        if (value.session.status !== "processing") {
          setPlanBusy("");
          const backgroundJob = value.session.plan_snapshot.background_job as
            | Partial<BrainstormBackgroundJob>
            | undefined;
          if (backgroundJob?.status === "failed" || backgroundJob?.status === "cancelled") {
            setError(backgroundJob.error_message || "后台任务失败，请重新提交。");
            setNotice(null);
          } else {
            setError(null);
            const normalizationWarnings = Array.isArray(
              value.session.plan_snapshot.normalization_warnings,
            )
              ? value.session.plan_snapshot.normalization_warnings.map(String)
              : [];
            setNotice(
              backgroundJob?.kind === "plan_discovery"
                ? `后台研究方向生成已完成，候选方向已刷新。${normalizationWarnings.length ? ` ${normalizationWarnings.join("；")}` : ""}`
                : "后台方案生成已完成，技术路线和实验方案已刷新。",
            );
          }
          if (projectId) {
            void apiRequest<{ sessions: BrainstormSession[] }>(
              `/brainstorm/sessions?project_id=${projectId}`,
              { cache: "no-store" },
            ).then((sessionsValue) => {
              setSessions(sessionsValue.sessions);
            });
          }
        }
      }).catch((value: unknown) => {
        if (active) setError(value instanceof Error ? value.message : "生成状态刷新失败");
      });
    }, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [processingSessionId, projectId]);

  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
  }, [detail?.messages.length, sending]);

  async function refreshSessions(projectId: string, selectId?: string) {
    const value = await apiRequest<{ sessions: BrainstormSession[] }>(
      `/brainstorm/sessions?project_id=${projectId}`,
      { cache: "no-store" },
    );
    setSessions(value.sessions);
    if (selectId) await openSession(selectId);
  }

  async function openSession(sessionId: string) {
    setError(null);
    setMessage("");
    try {
      const value = await apiRequest<BrainstormDetail>(`/brainstorm/sessions/${sessionId}`);
      setDetail(value);
      if (value.session.workflow === "plan") hydratePlanForm(value);
      const backgroundJob = value.session.plan_snapshot.background_job as
        | Partial<BrainstormBackgroundJob>
        | undefined;
      if (backgroundJob?.status === "failed" || backgroundJob?.status === "cancelled") {
        setError(backgroundJob.error_message || "后台任务失败，请重新提交。");
      }
    } catch (value) {
      setError(value instanceof Error ? value.message : "会话加载失败");
    }
  }

  function hydratePlanForm(value: BrainstormDetail) {
    const snapshot = value.session.plan_snapshot;
    const profile = (snapshot.preference_profile ?? {}) as Record<string, unknown>;
    const answers = (snapshot.preference_answers ?? {}) as Record<string, string | string[]>;
    setSelectedDirection(String(snapshot.selected_direction_id ?? ""));
    setObjectiveType(String(profile.objective_type ?? ""));
    setModelSystem(String(profile.model_system ?? ""));
    setBudgetLevel((profile.budget_level ?? "") as typeof budgetLevel);
    setTimelineWeeks(profile.timeline_weeks ? String(profile.timeline_weeks) : "");
    setSampleAvailability(String(profile.sample_availability ?? ""));
    setRiskTolerance((profile.risk_tolerance ?? "") as typeof riskTolerance);
    setPreferenceFreeText(String(profile.free_text ?? ""));
    setPreferenceAnswers(answers);
  }

  function beginRename(session: BrainstormSession) {
    setRenamingSessionId(session.id);
    setRenamingTitle(session.title);
    setSessionMenuId(null);
  }

  async function renameSession(event: FormEvent, sessionId: string) {
    event.preventDefault();
    const nextTitle = renamingTitle.trim();
    if (!library || !nextTitle) return;
    setSessionActionBusy(sessionId); setError(null);
    try {
      await apiRequest<BrainstormSession>(`/brainstorm/sessions/${sessionId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: nextTitle }),
      });
      setRenamingSessionId(null);
      setNotice("会话已重命名。");
      await refreshSessions(library.project_id, detail?.session.id === sessionId ? sessionId : undefined);
    } catch (value) { setError(value instanceof Error ? value.message : "会话重命名失败"); }
    finally { setSessionActionBusy(null); }
  }

  async function deleteSession(session: BrainstormSession) {
    if (!library || !window.confirm(`确定删除“${session.title}”吗？会话会先进入回收站。`)) return;
    setSessionActionBusy(session.id); setError(null); setSessionMenuId(null);
    try {
      await apiRequest(`/brainstorm/sessions/${session.id}`, { method: "DELETE" });
      if (detail?.session.id === session.id) setDetail(null);
      setNotice("会话已移入回收站，可在“记忆与回收站”中恢复。");
      await refreshSessions(library.project_id);
    } catch (value) { setError(value instanceof Error ? value.message : "会话删除失败"); }
    finally { setSessionActionBusy(null); }
  }

  async function createSession(event: FormEvent) {
    event.preventDefault();
    if (!library) return;
    setCreating(true);
    setError(null);
    try {
      const created = await apiRequest<BrainstormSession>("/brainstorm/sessions", {
        method: "POST",
        body: JSON.stringify({
          project_id: library.project_id,
          mode,
          title: title.trim() || null,
          collection_id: collectionId || null,
          allow_pubmed_search: allowPubmed,
          model_processing_allowed: allowModelProcessing,
          workflow: mode === "exploration" ? workflow : "classic",
          allow_web_search: mode === "exploration" && workflow === "plan" && allowWebSearch,
          model_depth: modelDepth,
          max_context_tokens: maxContextTokens,
          agent_background: agentBackground.trim(),
        }),
      });
      setTitle("");
      setAgentBackground("");
      setNotice(`已创建${mode === "exploration" ? "课题探索" : "课题完善"}会话 ${created.session_number}。`);
      await refreshSessions(library.project_id, created.id);
    } catch (value) {
      setError(value instanceof Error ? value.message : "会话创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function uploadSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = event.currentTarget;
    const body = new FormData(form);
    setUploading(true);
    setError(null);
    try {
      const response = await fetch(
        `${API_URL}/brainstorm/sessions/${detail.session.id}/source`,
        { method: "POST", body },
      );
      if (!response.ok) {
        const payload = (await response.json()) as { detail?: string };
        throw new Error(payload.detail ?? `课题文件上传失败（${response.status}）`);
      }
      form.reset();
      setNotice("原始课题方案已只读保存；后续改进会生成新版本。 ");
      await openSession(detail.session.id);
    } catch (value) {
      setError(value instanceof Error ? value.message : "课题文件上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!detail || message.trim().length < 2) return;
    setSending(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiRequest<BrainstormTurn>(
        `/brainstorm/sessions/${detail.session.id}/messages`,
        { method: "POST", body: JSON.stringify({ content: message.trim() }) },
      );
      setMessage("");
      setNotice(
        `已生成版本 ${result.version_number}；消耗 ${result.usage.total_tokens.toLocaleString()} Token，使用 ${result.evidence_count} 个证据块` +
          (result.auto_ingested_paper_ids.length
            ? `，并自动补充 ${result.auto_ingested_paper_ids.length} 篇实验参考文献。`
            : "。"),
      );
      await openSession(detail.session.id);
      if (library) await refreshSessions(library.project_id);
    } catch (value) {
      setError(value instanceof Error ? value.message : "多 Agent 分析失败");
    } finally {
      setSending(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (sending || message.trim().length < 2) return;
    event.currentTarget.form?.requestSubmit();
  }

  async function confirmSession() {
    if (!detail) return;
    setConfirming(true);
    setError(null);
    try {
      const value = await apiRequest<{ version_number: number }>(
        `/brainstorm/sessions/${detail.session.id}/confirm`,
        { method: "POST" },
      );
      setNotice(`已由用户确认并冻结为版本 ${value.version_number}。`);
      await openSession(detail.session.id);
      if (library) await refreshSessions(library.project_id);
    } catch (value) {
      setError(value instanceof Error ? value.message : "确认失败");
    } finally {
      setConfirming(false);
    }
  }

  async function dispatchToWorkbench(messageId: string) {
    if (!detail) return;
    setDispatchingMessageId(messageId);
    setError(null);
    setNotice(null);
    try {
      const value = await apiRequest<BrainstormTaskDispatch>(
        `/brainstorm/sessions/${detail.session.id}/messages/${messageId}/dispatch-to-workbench`,
        {
          method: "POST",
          body: JSON.stringify({
            work_date: new Date().toISOString().slice(0, 10),
            priority: "medium",
          }),
        },
      );
      setNotice(
        value.created_count
          ? `已按 ${value.sections_detected} 个实验章节拆分，并向科研工作台新增 ${value.created_count} 项任务。`
          : `该聊天的 ${value.sections_detected} 项实验任务已在科研工作台中，无需重复投送。`,
      );
    } catch (value) {
      setError(value instanceof Error ? value.message : "投送科研工作台失败");
    } finally {
      setDispatchingMessageId(null);
    }
  }

  async function restoreVersion(versionId: string, versionNumber: number) {
    if (!detail || !window.confirm(`确定从 V${versionNumber} 创建还原分支吗？现有历史不会被删除。`)) return;
    setRestoringVersionId(versionId); setError(null); setNotice(null);
    try {
      const value = await apiRequest<{ version_number: number }>(`/brainstorm/sessions/${detail.session.id}/versions/${versionId}/restore`, { method: "POST" });
      await openSession(detail.session.id);
      setNotice(`已从 V${versionNumber} 创建非破坏性还原分支 V${value.version_number}。`);
      if (library) await refreshSessions(library.project_id);
    } catch (value) { setError(value instanceof Error ? value.message : "还原失败"); }
    finally { setRestoringVersionId(null); }
  }

  async function regenerateMessage(messageId: string) {
    if (!detail) return;
    setRegeneratingMessageId(messageId); setError(null); setNotice(null);
    try {
      const value = await apiRequest<BrainstormTurn>(`/brainstorm/sessions/${detail.session.id}/regenerate`, { method: "POST", body: JSON.stringify({ message_id: messageId }) });
      await openSession(detail.session.id);
      setNotice(`已重新生成版本 ${value.version_number}，本次消耗 ${value.usage.total_tokens.toLocaleString()} Token。`);
      if (library) await refreshSessions(library.project_id);
    } catch (value) { setError(value instanceof Error ? value.message : "重新生成失败"); }
    finally { setRegeneratingMessageId(null); }
  }

  function messageUsage(messageId: string) {
    return detail?.usage.turns.find((item) => item.message_id === messageId);
  }

  async function discoverDirections() {
    if (!detail || planSeed.trim().length < 2) return;
    setPlanBusy("discover"); setError(null); setNotice(null);
    try {
      await apiRequest<BrainstormBackgroundJob>(`/brainstorm/sessions/${detail.session.id}/plan/discover`, { method: "POST", body: JSON.stringify({ seed_interest: planSeed.trim(), max_directions: 4 }) });
      await openSession(detail.session.id);
      setNotice("研究方向已转入后台生成；现在可以切换到其他模块，返回后会自动刷新结果。");
    } catch (value) { setError(value instanceof Error ? value.message : "研究方向生成失败"); }
    finally { setPlanBusy(""); }
  }

  async function selectDirectionAndPrefetch(directionId: string) {
    setSelectedDirection(directionId);
    if (!detail) return;
    void apiRequest<{ status: string; prefetched_paper_ids: string[]; error?: string }>(
      `/brainstorm/sessions/${detail.session.id}/plan/prefetch`,
      {
        method: "POST",
        body: JSON.stringify({ selected_direction_id: directionId }),
      },
    )
      .then(() => {
        void apiRequest<BrainstormDetail>(`/brainstorm/sessions/${detail.session.id}`, { cache: "no-store" })
          .then((updated) => setDetail(updated))
          .catch(() => {});
      })
      .catch(() => {});
  }

  async function savePreferences() {
    if (!detail || !selectedDirection) return;
    setPlanBusy("preferences"); setError(null); setNotice(null);
    try {
      const value = await apiRequest<BrainstormPlanSnapshot>(`/brainstorm/sessions/${detail.session.id}/plan/preferences`, { method: "PUT", body: JSON.stringify({ selected_direction_id: selectedDirection, objective_type: objectiveType || null, model_system: modelSystem || null, endpoint_priority: [], budget_level: budgetLevel || null, timeline_weeks: timelineWeeks ? Number(timelineWeeks) : null, sample_availability: sampleAvailability || null, risk_tolerance: riskTolerance || null, must_have_constraints: [], free_text: preferenceFreeText, answers: preferenceAnswers }) });
      await openSession(detail.session.id);
      setNotice(value.missing_fields.length ? `偏好已保存；未填写的 ${value.missing_fields.join("、")} 将作为未知约束，仍可直接生成。` : "偏好信息完整，可以一键生成方案。");
    } catch (value) { setError(value instanceof Error ? value.message : "偏好保存失败"); }
    finally { setPlanBusy(""); }
  }

  async function generatePlanProposal() {
    if (!detail || !selectedDirection) return;
    setPlanBusy("generate"); setError(null); setNotice(null);
    try {
      await apiRequest<BrainstormBackgroundJob>(`/brainstorm/sessions/${detail.session.id}/plan/generate`, {
        method: "POST",
        body: JSON.stringify({ selected_direction_id: selectedDirection, objective_type: objectiveType || null, model_system: modelSystem || null, endpoint_priority: [], budget_level: budgetLevel || null, timeline_weeks: timelineWeeks ? Number(timelineWeeks) : null, sample_availability: sampleAvailability || null, risk_tolerance: riskTolerance || null, must_have_constraints: [], free_text: preferenceFreeText, answers: preferenceAnswers }),
      });
      await openSession(detail.session.id);
      setNotice("技术路线和实验方案已转入后台生成；可以安全切换到其他模块。");
    } catch (value) {
      const message = value instanceof Error ? value.message : "方案生成失败";
      if (message.includes("already processing")) {
        setNotice("该方案仍在后台生成，页面会自动刷新结果，请勿重复提交。");
        await openSession(detail.session.id);
      } else setError(message);
    }
    finally { setPlanBusy(""); }
  }

  return (
    <>
      <header className="page-header brainstorm-header">
        <div>
          <span className="eyebrow">HUMAN-IN-THE-LOOP RESEARCH BRAINSTORM</span>
          <h1>课题头脑风暴</h1>
          <p>多 Agent 只提出有证据、可追踪的研究建议；关键选择始终由用户确认。</p>
        </div>
        <div className={`model-badge ${modelStatus?.configured ? "configured" : ""}`}>
          <span /> {modelStatus?.configured ? `${modelStatus.provider} · ${modelStatus.model}` : "需配置后端模型"}
        </div>
      </header>

      {error && <div aria-live="assertive" className="error-banner" role="alert">{error}</div>}
      {notice && <div aria-live="polite" className="success-banner" role="status">{notice}</div>}

      <section className="brainstorm-layout">
        <aside className="brainstorm-sidebar">
          <form className="brainstorm-create" onSubmit={createSession}>
            <span className="eyebrow">NEW SESSION</span>
            <div className="mode-switch">
              <button className={mode === "exploration" ? "active" : ""} onClick={() => setMode("exploration")} type="button">
                <Lightbulb size={16} />课题探索
              </button>
              <button className={mode === "refinement" ? "active" : ""} onClick={() => setMode("refinement")} type="button">
                <BookOpenCheck size={16} />课题完善
              </button>
            </div>
            {mode === "exploration" && <label>探索工作流<select value={workflow} onChange={(event) => setWorkflow(event.target.value as "classic" | "plan")}><option value="plan">Plan 模式：先选方向和偏好</option><option value="classic">经典模式：直接多轮讨论</option></select></label>}
            <label>会话标题（可选）<input onChange={(event) => setTitle(event.target.value)} placeholder="留空自动命名" value={title} /></label>
            <label>RAG 知识库（可选）
              <select onChange={(event) => setCollectionId(event.target.value)} value={collectionId}>
                <option value="">整个项目文献库</option>
                {collections.map((collection) => (
                  <option key={collection.id} value={collection.id}>{collection.name}（{collection.paper_count} 篇）</option>
                ))}
              </select>
            </label>
            <label className="checkbox-line"><input checked={allowPubmed} onChange={(event) => setAllowPubmed(event.target.checked)} type="checkbox" />每轮允许四来源自动补充文献（目标累计 30 篇）</label>
            {mode === "exploration" && workflow === "plan" && <label className="checkbox-line"><input checked={allowWebSearch} onChange={(event) => setAllowWebSearch(event.target.checked)} type="checkbox" />允许后端受控调用 Tavily（需已配置）</label>}
            <label className="checkbox-line"><input checked={allowModelProcessing} onChange={(event) => setAllowModelProcessing(event.target.checked)} type="checkbox" />允许将本会话必要内容发送到配置模型</label>
            <button className="advanced-settings-toggle" onClick={() => setAdvancedOpen((value) => !value)} type="button"><Settings2 size={14} />高级设置<span>{advancedOpen ? "收起" : "展开"}</span></button>
            {advancedOpen && <div className="brainstorm-advanced-settings">
              <label>模型思考深度<select value={modelDepth} onChange={(event) => setModelDepth(event.target.value as typeof modelDepth)}><option value="quick">Low · 快速</option><option value="balanced">High · 均衡</option><option value="deep">Deep · 系统审视</option><option value="max">Max · 官方最大强度</option></select></label>
              <label>最大上下文<select value={maxContextTokens} onChange={(event) => setMaxContextTokens(Number(event.target.value) as typeof maxContextTokens)}><option value={32768}>32K</option><option value={65536}>64K</option><option value={131072}>128K</option><option value={262144}>256K</option><option value={524288}>512K</option><option value={1000000}>1M · DeepSeek V4 上限</option></select><small>包含系统规则、Schema、长期记忆、证据与本轮输入，并为输出保留空间。</small></label>
              <label>Agent 背景设定<textarea maxLength={6000} onChange={(event) => setAgentBackground(event.target.value)} placeholder="例如：你是一组肿瘤免疫与统计遗传学研究者，面向博士生，以严谨但可执行的方式协作。不得覆盖证据与安全规则。" value={agentBackground} /><small>{agentBackground.length}/6000 · 只影响本会话协作视角</small></label>
            </div>}
            <button className="primary-action compact" disabled={creating || !library} type="submit">
              {creating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}创建会话
            </button>
          </form>
          <div className="session-list">
            <span className="eyebrow">SESSIONS</span>
            {sessions.map((session) => (
              <div className={`session-list-item ${detail?.session.id === session.id ? "active" : ""}`} key={session.id}>
                {renamingSessionId === session.id ? (
                  <form className="session-rename-form" onSubmit={(event) => void renameSession(event, session.id)}>
                    <input aria-label={`重命名 ${session.title}`} autoFocus maxLength={200} onChange={(event) => setRenamingTitle(event.target.value)} value={renamingTitle} />
                    <button disabled={sessionActionBusy === session.id || !renamingTitle.trim()} type="submit">保存</button>
                    <button onClick={() => setRenamingSessionId(null)} type="button">取消</button>
                  </form>
                ) : <>
                  <button className="session-open-button" onClick={() => void openSession(session.id)}>
                    <span>{session.mode === "exploration" ? <Lightbulb size={14} /> : <BookOpenCheck size={14} />}</span>
                    <div><strong>{session.title}</strong><small>会话{session.session_number} · {session.status}</small></div>
                  </button>
                  <button aria-label={`管理 ${session.title}`} className="session-menu-trigger" onClick={() => setSessionMenuId((value) => value === session.id ? null : session.id)}><MoreHorizontal size={14} /></button>
                  {sessionMenuId === session.id && <div className="session-item-menu">
                    <button onClick={() => beginRename(session)}><Pencil size={12} />重命名</button>
                    <button className="danger" disabled={sessionActionBusy === session.id} onClick={() => void deleteSession(session)}><Trash2 size={12} />删除</button>
                  </div>}
                </>}
              </div>
            ))}
            {!sessions.length && <p>尚无头脑风暴会话。</p>}
          </div>
        </aside>

        <section className="brainstorm-main">
          {!detail ? (
            <div className="empty-state brainstorm-empty"><BrainCircuit size={28} />创建或选择一个会话开始。</div>
          ) : (
            <>
              <header className="brainstorm-session-header">
                <div><span>{detail.session.mode === "exploration" ? "课题探索" : "课题完善"}</span><h2>{detail.session.title}</h2></div>
                <div><span>会话 {detail.session.session_number}</span><span>确认轮次 {detail.session.confirmation_round}</span><span><Gauge size={10} />{detail.session.model_depth === "quick" ? "Low" : detail.session.model_depth === "max" ? "Max" : detail.session.model_depth === "deep" ? "Deep" : "High"}</span><span>{detail.session.max_context_tokens === 1000000 ? "1M" : `${Math.round(detail.session.max_context_tokens / 1024)}K`} 上下文</span><span>{detail.usage.summary.total_tokens.toLocaleString()} Token</span></div>
              </header>
              {detail.session.agent_background && <details className="session-background"><summary><Settings2 size={13} />Agent 背景设定</summary><p>{detail.session.agent_background}</p></details>}

              {detail.session.workflow === "plan" && detail.session.mode === "exploration" && (
                <section className="plan-workflow-panel">
                  <header><div><span className="eyebrow">PLAN MODE</span><h3>先定方向，再按实验偏好生成方案</h3></div><span>{detail.session.phase}</span></header>
                  {Array.isArray(detail.session.plan_snapshot.normalization_warnings) && detail.session.plan_snapshot.normalization_warnings.length > 0 && (
                    <div className="success-banner">{detail.session.plan_snapshot.normalization_warnings.map(String).join("；")}</div>
                  )}
                  {!Array.isArray(detail.session.plan_snapshot.directions) || !(detail.session.plan_snapshot.directions as unknown[]).length ? (
                    <div className="plan-seed"><Compass size={21} /><textarea value={planSeed} onChange={(event) => setPlanSeed(event.target.value)} placeholder="描述你关注的疾病、机制、技术、已有观察或希望解决的问题…" /><button onClick={() => void discoverDirections()} disabled={planBusy !== "" || sessionProcessing || planSeed.trim().length < 2}>{planBusy === "discover" || sessionProcessing ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{sessionProcessing ? "后台生成研究方向中" : "生成热门研究方向"}</button></div>
                  ) : (
                    <>
                      <div className="plan-directions">
                        {(detail.session.plan_snapshot.directions as Array<Record<string, unknown>>).map((direction) => {
                          const dirId = String(direction.direction_id);
                          const isSelected = selectedDirection === dirId;
                          const prefetchJob = (detail.session.plan_snapshot.prefetch_job ?? null) as PrefetchJob | null;
                          return (
                            <button
                              className={isSelected ? "active" : ""}
                              key={dirId}
                              onClick={() => void selectDirectionAndPrefetch(dirId)}
                            >
                              <strong>{String(direction.title)}</strong>
                              <span>{String(direction.rationale)}</span>
                              <small>{Array.isArray(direction.why_hot) ? direction.why_hot.join(" · ") : ""}</small>
                              {isSelected && (
                                <div className="prefetch-badge">
                                  {prefetchJob?.status === "running" ? (
                                    <span className="prefetch-running"><LoaderCircle className="spin" size={12} /> 后台正在预取核心机制文献与 SOP...</span>
                                  ) : prefetchJob?.status === "succeeded" ? (
                                    <span className="prefetch-succeeded"><CheckCircle2 size={12} /> {prefetchJob.prefetched_paper_ids?.length > 0 ? `${prefetchJob.prefetched_paper_ids.length} 篇机制文献已就绪` : "文献已就绪"}</span>
                                  ) : prefetchJob?.status === "failed" ? (
                                    <span className="prefetch-failed"><ShieldAlert size={12} /> 预取文献网络波动，生成时将自动保底</span>
                                  ) : (
                                    <span className="prefetch-running"><LoaderCircle className="spin" size={12} /> 后台正在检索机制文献...</span>
                                  )}
                                </div>
                              )}
                            </button>
                          );
                        })}
                      </div>
                      {Array.isArray(detail.session.plan_snapshot.preference_questions) && (detail.session.plan_snapshot.preference_questions as Array<Record<string, unknown>>).length > 0 && (
                        <div className="generated-preference-questions">
                          <h4>模型需要你补充</h4>
                          {(detail.session.plan_snapshot.preference_questions as Array<Record<string, unknown>>).map((question) => {
                            const questionId = String(question.question_id);
                            const options = Array.isArray(question.options) ? question.options.map(String) : [];
                            const kind = String(question.kind);
                            return (
                              <label key={questionId}>
                                <span>{String(question.question)}{question.required ? " *" : ""}</span>
                                {kind === "single_choice" && options.length > 0 ? (
                                  <select value={String(preferenceAnswers[questionId] ?? "")} onChange={(event) => setPreferenceAnswers((current) => ({ ...current, [questionId]: event.target.value }))}><option value="">请选择</option>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select>
                                ) : kind === "multi_choice" && options.length > 0 ? (
                                  <div className="preference-choice-list">{options.map((option) => { const selected = Array.isArray(preferenceAnswers[questionId]) ? preferenceAnswers[questionId] as string[] : []; return <label key={option}><input type="checkbox" checked={selected.includes(option)} onChange={(event) => setPreferenceAnswers((current) => ({ ...current, [questionId]: event.target.checked ? [...selected, option] : selected.filter((item) => item !== option) }))} />{option}</label>; })}</div>
                                ) : kind === "scale" ? (
                                  <input type="range" min={1} max={5} value={Number(preferenceAnswers[questionId] ?? 3)} onChange={(event) => setPreferenceAnswers((current) => ({ ...current, [questionId]: event.target.value }))} />
                                ) : (
                                  <textarea value={String(preferenceAnswers[questionId] ?? "")} onChange={(event) => setPreferenceAnswers((current) => ({ ...current, [questionId]: event.target.value }))} placeholder="输入你的回答…" />
                                )}
                                {question.rationale ? <small>{String(question.rationale)}</small> : null}
                              </label>
                            );
                          })}
                        </div>
                      )}
                      <div className="preference-form-grid">
                        <label>研究目标<input value={objectiveType} onChange={(event) => setObjectiveType(event.target.value)} placeholder="机制验证、标志物、方法开发…" /></label>
                        <label>模型系统<input value={modelSystem} onChange={(event) => setModelSystem(event.target.value)} placeholder="细胞、队列、类器官、动物…" /></label>
                        <label>预算<select value={budgetLevel} onChange={(event) => setBudgetLevel(event.target.value as typeof budgetLevel)}><option value="">请选择</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
                        <label>周期（周）<input type="number" min={1} value={timelineWeeks} onChange={(event) => setTimelineWeeks(event.target.value)} /></label>
                        <label>样本/材料条件<input value={sampleAvailability} onChange={(event) => setSampleAvailability(event.target.value)} /></label>
                        <label>风险偏好<select value={riskTolerance} onChange={(event) => setRiskTolerance(event.target.value as typeof riskTolerance)}><option value="">请选择</option><option value="conservative">保守</option><option value="balanced">平衡</option><option value="aggressive">探索性</option></select></label>
                        <label className="wide">其他偏好或对问题选项的回答<textarea value={preferenceFreeText} onChange={(event) => setPreferenceFreeText(event.target.value)} /></label>
                      </div>
                      <footer><span>偏好完整度 {Math.round(Number(detail.session.plan_snapshot.readiness_score ?? 0) * 100)}% · 除研究方向外均可选</span><button onClick={() => void savePreferences()} disabled={!selectedDirection || planBusy !== "" || sessionProcessing}>保存偏好</button><button className="primary" onClick={() => void generatePlanProposal()} disabled={!selectedDirection || planBusy !== "" || sessionProcessing || detail.session.phase === "proposal_ready"}>{sessionProcessing || planBusy === "generate" ? <LoaderCircle className="spin" size={15} /> : <WandSparkles size={15} />}{sessionProcessing ? "后台生成中，请稍候" : detail.session.phase === "proposal_ready" ? "方案已生成" : "一键生成技术路线与实验方案"}</button></footer>
                      {(sessionProcessing || planBusy === "generate") && (
                        <div className="dual-stage-progress">
                          <div className="stage-step"><LoaderCircle className="spin" size={14} /> <span>[Step 1/2] 正在构建顶层假说、实验分组与决策门...</span></div>
                          <div className="stage-step-sub"><Sparkles size={12} /> <span>[Step 2/2] 随后将汇合全量新文献执行红队审查与双层重塑</span></div>
                        </div>
                      )}
                    </>
                  )}
                </section>
              )}

              {detail.session.mode === "refinement" && !hasOriginal && (
                <form className="proposal-upload" onSubmit={uploadSource}>
                  <FileUp size={22} />
                  <div><strong>上传原始课题方案</strong><span>支持 UTF-8 TXT/Markdown 或文本 PDF；原稿保存后不可覆盖。</span></div>
                  <input accept=".txt,.md,.markdown,.pdf,text/plain,text/markdown,application/pdf" name="file" required type="file" />
                  <button disabled={uploading} type="submit">{uploading ? "解析中" : "只读保存原稿"}</button>
                </form>
              )}

              <div className="brainstorm-thread" ref={threadRef}>
                {detail.messages.map((item) => (
                  <article className={`brainstorm-message ${item.role}`} key={item.id}>
                    <div className="message-avatar">{item.role === "user" ? <UserRound size={15} /> : <Bot size={15} />}</div>
                    <div className="message-body"><header><span>{item.role === "user" ? "你" : item.agent_name ?? "系统"}</span><small>消息 #{item.sequence_number}</small></header>
                    {item.role === "assistant" ? (
                      <div className="brainstorm-markdown">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            a: ({ href, children, ...props }) => {
                              const safeHref = href?.startsWith("https://") ? href : undefined;
                              return safeHref ? <a href={safeHref} target="_blank" rel="noreferrer" {...props}>{children}</a> : <span>{children}</span>;
                            },
                          }}
                        >
                          {formatCoordinatorMarkdown(item.content, item.payload)}
                        </ReactMarkdown>
                      </div>
                    ) : (
                      <p>{formatUserMessageContent(item.content)}</p>
                    )}
                    {item.role === "assistant" && (
                      <footer className="brainstorm-message-actions">
                        {messageUsage(item.id) && <span className="message-token-usage" title={`输入 ${messageUsage(item.id)?.prompt_tokens.toLocaleString()} · 输出 ${messageUsage(item.id)?.completion_tokens.toLocaleString()}`}><Gauge size={12} />{messageUsage(item.id)?.total_tokens.toLocaleString()} Token{messageUsage(item.id)?.estimated_token_events ? " · 估算" : ""}</span>}
                        <button disabled={regeneratingMessageId !== null || detail.session.status === "finalized"} onClick={() => void regenerateMessage(item.id)} type="button">
                          {regeneratingMessageId === item.id ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}{regeneratingMessageId === item.id ? "重新生成中" : "重新生成"}
                        </button>
                        <button disabled={dispatchingMessageId !== null} onClick={() => void dispatchToWorkbench(item.id)} type="button">
                          {dispatchingMessageId === item.id ? <LoaderCircle className="spin" size={13} /> : <ListTodo size={13} />}
                          {dispatchingMessageId === item.id ? "拆分投送中" : "按章节投送至科研工作台"}
                        </button>
                      </footer>
                    )}
                    </div>
                  </article>
                ))}
                {sending && <article className="brainstorm-message assistant pending"><div className="message-avatar"><Bot size={15} /></div><div className="message-body"><header><span>科研协调 Agent</span><small>分析中</small></header><div className="typing-indicator"><i /><i /><i /><span>正在检索证据并协调多个 Agent…</span></div></div></article>}
                {!detail.messages.length && <div className="empty-thread">描述研究方向、已有观察、样本和资源约束。</div>}
              </div>

              {latestAssistant && (
                <section className="brainstorm-result">
                  <div className="evidence-foundation-bar">
                    <Sparkles size={14} />
                    <strong>证据底座构成：</strong>
                    <span>{detail.session.collection_id ? "指定 RAG 知识库" : "全项目文献库"} + {detail.session.allow_pubmed_search ? "自动机制扩充" : "本地已有文献"}</span>
                    {Boolean((detail.session.plan_snapshot?.prefetch_job as PrefetchJob | null)?.prefetched_paper_ids?.length) && (
                      <small>（已深度融合 {(detail.session.plan_snapshot?.prefetch_job as PrefetchJob).prefetched_paper_ids.length} 篇前置预取机制文献）</small>
                    )}
                  </div>
                  {generationQuality.mode && (
                    <div className={`generation-quality ${generationQuality.mode}`}>
                      <strong>{generationQuality.mode === "model_complete" ? "模型完整生成" : "保底辅助生成"}</strong>
                      <span>
                        {generationQuality.mode === "model_complete"
                          ? "所有 Agent 均使用完整模型结构化输出。"
                          : `以下 Agent 的模型输出不完整，已使用确定性保底：${(generationQuality.fallback_agents ?? []).join("、") || "未知阶段"}`}
                      </span>
                      {generationQuality.draft_loops && Object.keys(generationQuality.draft_loops).length > 0 && (
                        <small>
                          多轮扩充：{Object.entries(generationQuality.draft_loops).map(([agent, value]) => `${agent} ${value.refinement_rounds ?? 0} 轮（${value.stop_reason ?? "完成"}）`).join("；")}
                        </small>
                      )}
                    </div>
                  )}
                  <div className="agent-grid">
                    <article><FlaskConical size={18} /><strong>科学问题 Agent</strong><span>创新性、可实施性与可证伪假设</span></article>
                    <article><BrainCircuit size={18} /><strong>实验路线 Agent</strong><span>RAG 证据、材料候选与技术路线</span></article>
                    <article><Microscope size={18} /><strong>方法学 Agent</strong><span>步骤展开、对照、质控、判定与排错</span></article>
                    <article><ShieldAlert size={18} /><strong>创新批评 Agent</strong><span>不足、偏倚、替代解释与安全</span></article>
                  </div>
                  {coordinator.technical_route_mermaid && <MermaidDiagram code={coordinator.technical_route_mermaid} />}
                  {!!experiment.materials?.length && (
                    <div className="materials-table">
                      <h3>实验材料与产品候选</h3>
                      <table><thead><tr><th>材料</th><th>规格/用途</th><th>厂商/型号</th><th>核验状态</th></tr></thead>
                        <tbody>{experiment.materials.map((item, index) => (
                          <tr key={`${item.name}-${index}`}><td>{item.name}</td><td>{item.specification}</td><td>{[item.manufacturer, item.catalog_model].filter(Boolean).join(" · ") || "未指定"}</td><td>{item.verification_status}<small>{item.verification_note}</small></td></tr>
                        ))}</tbody></table>
                    </div>
                  )}
                  {!!method.modules?.length && (
                    <section className="method-agent-panel">
                      <header><div><span className="eyebrow">METHOD AGENT</span><h3>附录：具体实验方法规程（SOP）</h3></div><Microscope size={19} /></header>
                      {method.method_overview && (
                        <div className="brainstorm-markdown method-overview-markdown">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              a: ({ href, children, ...props }) => {
                                const safeHref = href?.startsWith("https://") ? href : undefined;
                                return safeHref ? <a href={safeHref} target="_blank" rel="noreferrer" {...props}>{children}</a> : <span>{children}</span>;
                              },
                            }}
                          >
                            {formatMarkdownContent(method.method_overview)}
                          </ReactMarkdown>
                        </div>
                      )}
                      <div className="method-module-list">
                        {method.modules.map((module, index) => (
                          <details key={`${module.title}-${index}`} open={index === 0}>
                            <summary><span>{String(index + 1).padStart(2, "0")}</span><strong>{module.title ?? `方法模块 ${index + 1}`}</strong></summary>
                            <div>
                              {module.objective && (
                                <div className="module-field">
                                  <b>目的</b>
                                  <div className="brainstorm-markdown inline-markdown">
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(module.objective)}</ReactMarkdown>
                                  </div>
                                </div>
                              )}
                              {module.principle && (
                                <div className="module-field">
                                  <b>原理</b>
                                  <div className="brainstorm-markdown inline-markdown">
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(module.principle)}</ReactMarkdown>
                                  </div>
                                </div>
                              )}
                              {!!module.staged_procedure?.length && (
                                <section>
                                  <strong>阶段化步骤</strong>
                                  <ol>{module.staged_procedure.map((value) => <li key={value}><ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(value)}</ReactMarkdown></li>)}</ol>
                                </section>
                              )}
                              {!!module.controls?.length && (
                                <section>
                                  <strong>分组与对照</strong>
                                  <ul>{module.controls.map((value) => <li key={value}><ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(value)}</ReactMarkdown></li>)}</ul>
                                </section>
                              )}
                              {!!module.quality_control?.length && (
                                <section>
                                  <strong>质控</strong>
                                  <ul>{module.quality_control.map((value) => <li key={value}><ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(value)}</ReactMarkdown></li>)}</ul>
                                </section>
                              )}
                              {!!module.acceptance_and_decision_criteria?.length && (
                                <section>
                                  <strong>验收与决策</strong>
                                  <ul>{module.acceptance_and_decision_criteria.map((value) => <li key={value}><ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(value)}</ReactMarkdown></li>)}</ul>
                                </section>
                              )}
                              {!!module.failure_modes_and_troubleshooting?.length && (
                                <section>
                                  <strong>失败模式与排错</strong>
                                  <ul>{module.failure_modes_and_troubleshooting.map((value) => <li key={value}><ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(value)}</ReactMarkdown></li>)}</ul>
                                </section>
                              )}
                              {!!module.parameter_gaps?.length && (
                                <section className="method-gaps">
                                  <strong>待本地 SOP / 预实验确定</strong>
                                  <ul>{module.parameter_gaps.map((value) => <li key={value}><ReactMarkdown remarkPlugins={[remarkGfm]}>{formatMarkdownContent(value)}</ReactMarkdown></li>)}</ul>
                                </section>
                              )}
                            </div>
                          </details>
                        ))}
                      </div>
                    </section>
                  )}
                  {!!coordinator.confirmation_questions?.length && (
                    <div className="confirmation-box"><strong>需要你确认</strong><ol>{coordinator.confirmation_questions.map((question) => <li key={question}>{question}</li>)}</ol></div>
                  )}
                  {!!coordinator.safety_flags?.length && (
                    <div className="safety-flags"><ShieldAlert size={17} /><div><strong>伦理/安全提醒</strong>{coordinator.safety_flags.join("；")}</div></div>
                  )}
                </section>
              )}

              {!!detail.versions.length && (
                <section className="version-strip">
                  <strong><History size={13} />方案版本 / 还原点</strong>
                  {detail.versions.map((version) => (
                    <button className={version.kind} disabled={restoringVersionId !== null || detail.session.status === "finalized" || version.id === latestVersion?.id} key={version.id} onClick={() => void restoreVersion(version.id, version.version_number)} title={version.id === latestVersion?.id ? "当前版本" : `从 V${version.version_number} 创建还原分支`} type="button">{restoringVersionId === version.id ? <LoaderCircle className="spin" size={11} /> : <RotateCcw size={11} />}V{version.version_number} · {version.kind}{version.source_filename ? ` · ${version.source_filename}` : ""}</button>
                  ))}
                </section>
              )}

              {detail.session.mode === "refinement" && originalVersion && (
                <section className="version-compare">
                  <article>
                    <header><strong>原始方案 · 只读</strong><span>V{originalVersion.version_number}</span></header>
                    <div className="brainstorm-markdown version-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {formatMarkdownContent(originalVersion.content)}
                      </ReactMarkdown>
                    </div>
                  </article>
                  <article>
                    <header><strong>最新派生方案</strong><span>{latestVersion && latestVersion.id !== originalVersion.id ? `V${latestVersion.version_number}` : "等待生成"}</span></header>
                    <div className="brainstorm-markdown version-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {formatMarkdownContent(latestVersion && latestVersion.id !== originalVersion.id ? latestVersion.content : "发送改进要求后，系统会在此创建新版本；原稿保持不变。")}
                      </ReactMarkdown>
                    </div>
                  </article>
                </section>
              )}

              <form className="brainstorm-composer" onSubmit={sendMessage}>
                <div className="composer-icon"><MessageSquareText size={19} /></div>
                <div className="composer-input"><textarea disabled={!detail.session.model_processing_allowed || detail.session.status === "finalized" || (detail.session.mode === "refinement" && !hasOriginal) || sending} onChange={(event) => setMessage(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder={!detail.session.model_processing_allowed ? "该会话未授权模型处理，请新建并勾选授权。" : detail.session.mode === "exploration" ? "描述课题方向、观察、模型和资源，或回答上轮确认问题…" : "说明希望改进的部分，或回答上轮确认问题…"} value={message} /><footer><span>Enter 发送 · Shift + Enter 换行</span><small>{message.length}/12000</small></footer></div>
                <button aria-label="发送消息" disabled={sending || !detail.session.model_processing_allowed || detail.session.status === "finalized" || (detail.session.mode === "refinement" && !hasOriginal) || message.trim().length < 2} type="submit">{sending ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}</button>
              </form>
              {detail.session.status === "awaiting_confirmation" && (
                <button className="confirm-proposal" disabled={confirming} onClick={confirmSession}><CheckCircle2 size={16} />{confirming ? "确认中" : "确认并冻结当前方案"}</button>
              )}
            </>
          )}
        </section>
      </section>
    </>
  );
}
