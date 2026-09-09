"use client";

import {
  BookOpenText,
  BrainCircuit,
  CalendarRange,
  ChartNoAxesCombined,
  FileSearch,
  FlaskConical,
  LibraryBig,
  MemoryStick,
  MessageSquareText,
  Settings2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const navigation = [
  { label: "研究总览", icon: FlaskConical, href: "/" },
  { label: "用量仪表盘", icon: ChartNoAxesCombined, href: "/dashboard" },
  { label: "文献检索", icon: FileSearch, href: "/search" },
  { label: "个人文献库", icon: LibraryBig, href: "/library" },
  { label: "课题头脑风暴", icon: MessageSquareText, href: "/brainstorm" },
  { label: "证据问答", icon: BrainCircuit, href: "/evidence" },
  { label: "综述工作台", icon: BookOpenText, href: "/synthesis" },
  { label: "科研工作台", icon: CalendarRange, href: "/workbench" },
  { label: "记忆与回收站", icon: MemoryStick, href: "/memory" },
];

export function AppShell({ children }: Readonly<{ children: ReactNode }>) {
  const pathname = usePathname();

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          <div className="brand-mark"><Sparkles size={19} /></div>
          <div><strong>Science Buddy</strong><span>RESEARCH OS</span></div>
        </Link>

        <nav aria-label="主导航">
          <p className="nav-caption">工作区</p>
          {navigation.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link className={`nav-item ${active ? "active" : ""}`} href={item.href} key={item.href}>
                <item.icon size={18} />
                <span>{item.label}</span>
                {active && <span className="nav-dot" />}
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <Link className={`nav-item ${pathname.startsWith("/settings") ? "active" : ""}`} href="/settings">
            <Settings2 size={18} /><span>模型与隐私</span>
            {pathname.startsWith("/settings") && <span className="nav-dot" />}
          </Link>
          <div className="safety-note">
            <ShieldCheck size={17} />
            <p><strong>科研辅助模式</strong><span>不用于诊疗或临床决策</span></p>
          </div>
        </div>
      </aside>
      <section className="workspace">{children}</section>
    </main>
  );
}
