"use client";

import {
  ArrowRight,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDotDashed,
  ClipboardList,
  FilePenLine,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  apiRequest,
  type LibraryResponse,
  type WorkbenchOverview,
  type WorkbenchTask,
} from "@/lib/api";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

function dateKey(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthKey(value: Date) {
  return dateKey(value).slice(0, 7);
}

function monthLabel(value: Date) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long" }).format(value);
}

function calendarDays(value: Date) {
  const year = value.getFullYear();
  const month = value.getMonth();
  const leading = (new Date(year, month, 1).getDay() + 6) % 7;
  const total = new Date(year, month + 1, 0).getDate();
  return [
    ...Array.from({ length: leading }, () => null),
    ...Array.from({ length: total }, (_, index) => new Date(year, month, index + 1)),
  ];
}

export function WorkbenchDashboard() {
  const today = useMemo(() => new Date(), []);
  const [projectId, setProjectId] = useState("");
  const [month, setMonth] = useState(new Date(today.getFullYear(), today.getMonth(), 1));
  const [overview, setOverview] = useState<WorkbenchOverview | null>(null);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDate, setTaskDate] = useState(dateKey(today));
  const [noteTitle, setNoteTitle] = useState("");
  const [noteDate, setNoteDate] = useState(dateKey(today));
  const [priority, setPriority] = useState<WorkbenchTask["priority"]>("medium");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function loadOverview(targetProjectId: string, targetMonth: Date) {
    const result = await apiRequest<WorkbenchOverview>(
      `/workbench/overview?project_id=${targetProjectId}&month=${monthKey(targetMonth)}`,
    );
    setOverview(result);
  }

  useEffect(() => {
    let active = true;
    async function loadProject() {
      try {
        const library = await apiRequest<LibraryResponse>("/library");
        if (!active) return;
        setProjectId(library.project_id);
        await loadOverview(library.project_id, month);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "工作台加载失败");
      }
    }
    void loadProject();
    return () => {
      active = false;
    };
  }, [month]);

  const dayMap = useMemo(
    () => new Map((overview?.days ?? []).map((day) => [day.entry_date, day])),
    [overview],
  );
  const days = useMemo(() => calendarDays(month), [month]);
  const tasks = overview?.tasks ?? [];
  const done = tasks.filter((task) => task.status === "done").length;
  const progress = tasks.length ? Math.round((done / tasks.length) * 100) : 0;

  async function refresh() {
    if (projectId) await loadOverview(projectId, month);
  }

  async function createTask(event: React.FormEvent) {
    event.preventDefault();
    if (!projectId || !taskTitle.trim()) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest("/workbench/tasks", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          title: taskTitle,
          work_date: taskDate || null,
          priority,
        }),
      });
      setTaskTitle("");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "任务创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function updateTask(task: WorkbenchTask, status: WorkbenchTask["status"]) {
    setBusy(true);
    try {
      await apiRequest(`/workbench/tasks/${task.id}?project_id=${projectId}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "任务更新失败");
    } finally {
      setBusy(false);
    }
  }

  async function deleteTask(taskId: string) {
    setBusy(true);
    try {
      await apiRequest(`/workbench/tasks/${taskId}?project_id=${projectId}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "任务删除失败");
    } finally {
      setBusy(false);
    }
  }

  async function createNote(event: React.FormEvent) {
    event.preventDefault();
    if (!projectId || !noteTitle.trim()) return;
    setBusy(true);
    try {
      await apiRequest(`/workbench/days/${noteDate}`, {
        method: "PUT",
        body: JSON.stringify({
          project_id: projectId,
          title: noteTitle,
          content_markdown: "# 研究目标\n\n\n## 今日记录\n\n\n## 下一步\n\n- [ ] ",
        }),
      });
      window.location.href = `/workbench/${noteDate}`;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "笔记创建失败");
      setBusy(false);
    }
  }

  return (
    <div className="workbench-page">
      <header className="page-header workbench-header">
        <div>
          <span className="eyebrow">RESEARCH WORKBENCH</span>
          <h1>科研工作台</h1>
          <p>把每日实验、待办进展与课题笔记放在同一条时间线上。</p>
        </div>
        <div className="boundary-badge"><Sparkles size={15} /> 本地优先 · 自动保存</div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="workbench-overview-grid">
        <article className="workbench-calendar-card">
          <header>
            <div>
              <span className="eyebrow">CALENDAR</span>
              <h2>{monthLabel(month)}</h2>
            </div>
            <div className="calendar-nav">
              <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} aria-label="上个月"><ChevronLeft size={17} /></button>
              <button onClick={() => setMonth(new Date(today.getFullYear(), today.getMonth(), 1))}>今天</button>
              <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} aria-label="下个月"><ChevronRight size={17} /></button>
            </div>
          </header>
          <div className="calendar-weekdays">
            {WEEKDAYS.map((day) => <span key={day}>周{day}</span>)}
          </div>
          <div className="calendar-grid">
            {days.map((day, index) => {
              if (!day) return <span className="calendar-empty" key={`empty-${index}`} />;
              const key = dateKey(day);
              const summary = dayMap.get(key);
              const isToday = key === dateKey(today);
              return (
                <Link className={`calendar-day ${isToday ? "today" : ""} ${summary?.has_note ? "has-note" : ""}`} href={`/workbench/${key}`} key={key}>
                  <strong>{day.getDate()}</strong>
                  <span>{summary?.note_title ?? (summary?.tasks_total ? `${summary.tasks_done}/${summary.tasks_total} 任务` : "")}</span>
                  {(summary?.has_note || summary?.tasks_total) && <i />}
                </Link>
              );
            })}
          </div>
          <footer><CalendarDays size={15} /> 点击任意日期进入 Markdown 日笔记</footer>
        </article>

        <article className="workbench-task-card">
          <header>
            <div>
              <span className="eyebrow">PROGRESS</span>
              <h2>任务进展</h2>
            </div>
            <div className="progress-ring" style={{ "--progress": `${progress * 3.6}deg` } as React.CSSProperties}>
              <strong>{progress}%</strong>
            </div>
          </header>
          <form className="task-quick-add" onSubmit={createTask}>
            <input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} placeholder="添加下一项科研任务…" maxLength={300} />
            <div>
              <input type="date" value={taskDate} onChange={(event) => setTaskDate(event.target.value)} />
              <select value={priority} onChange={(event) => setPriority(event.target.value as WorkbenchTask["priority"])}>
                <option value="low">低优先级</option><option value="medium">中优先级</option><option value="high">高优先级</option>
              </select>
              <button disabled={busy || !taskTitle.trim()}><Plus size={15} /> 添加</button>
            </div>
          </form>
          <div className="task-progress-list">
            {tasks.slice(0, 9).map((task) => (
              <div className={`workbench-task ${task.status}`} key={task.id}>
                <button className="task-check" onClick={() => void updateTask(task, task.status === "done" ? "todo" : "done")} disabled={busy} aria-label="切换完成状态">
                  {task.status === "done" ? <Check size={14} /> : <CircleDotDashed size={14} />}
                </button>
                <div><strong>{task.title}</strong><span>{task.work_date ?? "待安排"} · {task.priority === "high" ? "高" : task.priority === "low" ? "低" : "中"}优先级{task.source_kind === "brainstorm_message" ? ` · 头脑风暴${task.source_section ? ` / ${task.source_section}` : ""}` : ""}</span></div>
                <select value={task.status} onChange={(event) => void updateTask(task, event.target.value as WorkbenchTask["status"])} disabled={busy} aria-label="任务状态">
                  <option value="todo">待开始</option><option value="in_progress">进行中</option><option value="done">已完成</option>
                </select>
                <button className="task-delete" onClick={() => void deleteTask(task.id)} disabled={busy} aria-label="删除任务"><Trash2 size={14} /></button>
              </div>
            ))}
            {!tasks.length && <div className="workbench-empty-small"><ClipboardList size={23} /><span>还没有任务，从一个可执行步骤开始。</span></div>}
          </div>
          <footer><span>{done} 项已完成</span><span>{tasks.length - done} 项待推进</span></footer>
        </article>
      </section>

      <section className="workbench-notes-section">
        <div className="workbench-note-creator">
          <div className="note-creator-copy"><FilePenLine size={25} /><span className="eyebrow">TOPIC NOTES</span><h2>创建课题笔记</h2><p>从研究目标开始，逐日沉淀实验记录、表格、图片与 OCR 文本。</p></div>
          <form onSubmit={createNote}>
            <label>笔记日期<input type="date" value={noteDate} onChange={(event) => setNoteDate(event.target.value)} /></label>
            <label>课题标题<input value={noteTitle} onChange={(event) => setNoteTitle(event.target.value)} placeholder="例如：噬菌体反防御系统验证" maxLength={240} /></label>
            <button disabled={busy || !noteTitle.trim()}><Plus size={16} /> 创建并进入编辑器</button>
          </form>
        </div>
        <div className="recent-notes">
          <header><div><span className="eyebrow">RECENT</span><h2>最近笔记</h2></div><span>{overview?.recent_notes.length ?? 0} 篇</span></header>
          <div>
            {(overview?.recent_notes ?? []).map((note) => (
              <Link href={`/workbench/${note.entry_date}`} key={note.id}>
                <span>{new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(new Date(`${note.entry_date}T00:00:00`))}</span>
                <strong>{note.title}</strong>
                <small>{note.content_markdown.replace(/[#>*|`\-[\]]/g, " ").trim().slice(0, 72) || "打开并开始记录"}</small>
                <ArrowRight size={15} />
              </Link>
            ))}
            {!overview?.recent_notes.length && <div className="workbench-empty-small"><FilePenLine size={23} /><span>创建第一篇课题笔记后会显示在这里。</span></div>}
          </div>
        </div>
      </section>
    </div>
  );
}
