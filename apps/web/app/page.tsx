import {
  Activity,
  ArrowUpRight,
  BrainCircuit,
  CheckCircle2,
  CircleDashed,
  Database,
  Network,
  Quote,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";

export const dynamic = "force-dynamic";

type HealthState = "online" | "offline";

async function getApiHealth(): Promise<HealthState> {
  const apiUrl =
    process.env.API_INTERNAL_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://127.0.0.1:8000/api/v1";
  try {
    const response = await fetch(`${apiUrl}/health/live`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    return response.ok ? "online" : "offline";
  } catch {
    return "offline";
  }
}

const stages = [
  {
    id: "01",
    title: "文献入库",
    description: "PubMed、Europe PMC 与个人 PDF 的结构化入口。",
    icon: Database,
    status: "已实现",
  },
  {
    id: "02",
    title: "混合 RAG",
    description: "SQLite FTS5、跨语言 E5、MeSH 与轻量图的加权融合。",
    icon: Network,
    status: "FTS 已实现",
  },
  {
    id: "03",
    title: "证据校验",
    description: "Evidence ID、原文跨度和声明关系的可信链。",
    icon: ShieldCheck,
    status: "状态机就绪",
  },
  {
    id: "04",
    title: "综述生成",
    description: "仅使用已校验声明构建中英文研究综述。",
    icon: Quote,
    status: "待实现",
  },
];

export default async function Home() {
  const apiHealth = await getApiHealth();
  const isOnline = apiHealth === "online";

  return (
    <>
        <header className="topbar">
          <div>
            <span className="eyebrow">LOCAL RESEARCH WORKSPACE</span>
            <h1>让每个科研结论，都能回到原文。</h1>
          </div>
          <div className={`api-status ${isOnline ? "online" : "offline"}`}>
            <span className="pulse" />
            API {isOnline ? "在线" : "未连接"}
          </div>
        </header>

        <section className="hero-grid">
          <article className="hero-card">
            <div className="hero-copy">
              <span className="section-tag"><Activity size={14} /> 项目基线 · v0.1</span>
              <h2>可信文献研究，<br /><em>从证据开始。</em></h2>
              <p>PubMed 与 Europe PMC 检索、跨源去重、摘要入库和可追溯分块已经接通。</p>
              <Link className="primary-action" href="/search">开始检索文献 <ArrowUpRight size={17} /></Link>
            </div>
            <div className="orbit" aria-hidden="true">
              <span className="orbit-ring ring-one" />
              <span className="orbit-ring ring-two" />
              <span className="orbit-core"><BrainCircuit size={35} /></span>
              <span className="orbit-node node-one" />
              <span className="orbit-node node-two" />
              <span className="orbit-node node-three" />
            </div>
          </article>

          <article className="status-card">
            <div className="card-heading"><div><span>系统状态</span><h3>基础设施</h3></div><Activity size={20} /></div>
            <dl className="status-list">
              <div><dt>Web 工作台</dt><dd className="ready"><CheckCircle2 size={15} /> 就绪</dd></div>
              <div><dt>FastAPI</dt><dd className={isOnline ? "ready" : "waiting"}>{isOnline ? <CheckCircle2 size={15} /> : <CircleDashed size={15} />} {isOnline ? "在线" : "待启动"}</dd></div>
              <div><dt>SQLite / FTS5</dt><dd className="ready"><CheckCircle2 size={15} /> 本地持久化</dd></div>
              <div><dt>Redis / 三级缓存</dt><dd className="waiting"><CircleDashed size={15} /> 随服务启动</dd></div>
            </dl>
          </article>
        </section>

        <section className="pipeline-section">
          <div className="section-heading">
            <div><span className="eyebrow">IMPLEMENTATION PIPELINE</span><h2>可信链路</h2></div>
            <p>当前页面只展示真实工程状态，不生成虚假的科研答案。</p>
          </div>
          <div className="stage-grid">
            {stages.map((stage) => (
              <article className="stage-card" key={stage.id}>
                <div className="stage-top"><span>{stage.id}</span><stage.icon size={21} /></div>
                <h3>{stage.title}</h3>
                <p>{stage.description}</p>
                <footer><span className="status-pill">{stage.status}</span><ArrowUpRight size={16} /></footer>
              </article>
            ))}
          </div>
        </section>
    </>
  );
}
