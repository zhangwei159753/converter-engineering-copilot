import { Activity, Archive, AudioWaveform, BookOpen, CircleHelp, ClipboardCheck, Settings2 } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

export default function AppShell() {
  return (
    <div className="app-frame">
      <aside className="rail">
        <NavLink className="brand-mark" to="/" aria-label="Gridline 工作台">G<span>.</span></NavLink>
        <div className="rail-rule" />
        <NavLink className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`} to="/" title="工作台" aria-label="工作台"><AudioWaveform size={19} /></NavLink>
        <NavLink className="rail-link muted" to="/" title="知识库" aria-label="知识库"><BookOpen size={19} /></NavLink>
        <NavLink className="rail-link muted" to="/" title="任务归档" aria-label="任务归档"><Archive size={19} /></NavLink>
        <NavLink className={({ isActive }) => `rail-link ${isActive ? "active" : "muted"}`} to="/evaluations" title="人工评测" aria-label="人工评测"><ClipboardCheck size={19} /></NavLink>
        <div className="rail-spacer" />
        <NavLink className="rail-link muted" to="/" title="帮助" aria-label="帮助"><CircleHelp size={19} /></NavLink>
        <NavLink className="rail-link muted" to="/" title="设置" aria-label="设置"><Settings2 size={19} /></NavLink>
        <div className="avatar">林</div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title"><span className="eyebrow">GRIDLINE / ENGINEERING INTELLIGENCE</span><span>变流器工程 Copilot</span></div>
          <div className="service-state"><span className="live-dot" />本地工作空间<span className="topbar-divider" /><Activity size={15} />演示环境</div>
        </header>
        <main className="page-container"><Outlet /></main>
        <footer className="app-footer"><span>GRIDLINE ENGINEERING SYSTEM</span><span>证据先行 · 人工复核 · 安全操作</span></footer>
      </div>
    </div>
  );
}
