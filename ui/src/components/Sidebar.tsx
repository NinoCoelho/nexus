import React, { useEffect, useRef, useState } from "react";
import { deleteSession, exportSession, getSessions, importSession, patchSession, type SessionSummary } from "../api";
import "./Sidebar.css";

type View = "chat" | "vault" | "kanban" | "graph";

interface Props {
  view: View;
  onViewChange: (v: View) => void;
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onNewChat: () => void;
  onOpenSettings: () => void;
  sessionsRevision: number;
  onSessionsRevisionBump: () => void;
}

function fmtRelative(raw: string | number | undefined): string {
  if (raw == null) return "";
  let ts: number;
  if (typeof raw === "number") {
    // Backend sends unix seconds; Date expects ms.
    ts = raw < 1e12 ? raw * 1000 : raw;
  } else {
    const parsed = new Date(raw).getTime();
    if (isNaN(parsed)) return "";
    ts = parsed;
  }
  const diff = Date.now() - ts;
  if (diff < 0) return "just now";
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

// ── icons ─────────────────────────────────────────────────────────────────────

function IconChat() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 3H2a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h4l4 3 4-3h4a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1z" />
    </svg>
  );
}

function IconVault() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <line x1="3" y1="8" x2="17" y2="8" />
      <line x1="8" y1="8" x2="8" y2="17" />
    </svg>
  );
}

function IconKanban() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="4" width="4" height="12" rx="1" />
      <rect x="8" y="4" width="4" height="8" rx="1" />
      <rect x="14" y="4" width="4" height="10" rx="1" />
    </svg>
  );
}

function IconGraph() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="4" r="2" />
      <circle cx="3" cy="16" r="2" />
      <circle cx="17" cy="16" r="2" />
      <line x1="10" y1="6" x2="3" y2="14" />
      <line x1="10" y1="6" x2="17" y2="14" />
      <line x1="5" y1="16" x2="15" y2="16" />
    </svg>
  );
}

function IconGear() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2.5v2.5 M10 15v2.5 M2.5 10h2.5 M15 10h2.5 M4.7 4.7l1.8 1.8 M13.5 13.5l1.8 1.8 M4.7 15.3l1.8-1.8 M13.5 6.5l1.8-1.8" />
    </svg>
  );
}

function IconCollapse({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {collapsed ? (
        <>
          <polyline points="7 4 13 10 7 16" />
          <polyline points="12 4 18 10 12 16" />
        </>
      ) : (
        <>
          <polyline points="13 4 7 10 13 16" />
          <polyline points="8 4 2 10 8 16" />
        </>
      )}
    </svg>
  );
}

const VIEWS: { id: View; label: string; Icon: () => React.ReactElement }[] = [
  { id: "chat",   label: "Chat",   Icon: IconChat },
  { id: "vault",  label: "Vault",  Icon: IconVault },
  { id: "kanban", label: "Kanban", Icon: IconKanban },
  { id: "graph",  label: "Graph",  Icon: IconGraph },
];

export default function Sidebar({
  view,
  onViewChange,
  activeSessionId,
  onSessionSelect,
  onNewChat,
  onOpenSettings,
  sessionsRevision,
  onSessionsRevisionBump,
}: Props) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem("sidebar-collapsed") === "true"; }
    catch { return false; }
  });

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsError, setSessionsError] = useState(false);

  // Rename inline state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // Context menu
  const [menuId, setMenuId] = useState<string | null>(null);

  // Import file input ref
  const importInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    localStorage.setItem("sidebar-collapsed", String(collapsed));
  }, [collapsed]);

  useEffect(() => {
    setSessionsError(false);
    getSessions(20)
      .then((s) => setSessions(s.sort((a, b) => {
        // updated_at is unix seconds (int) from the backend. Backend already
        // orders by updated_at DESC but we sort again defensively.
        const av = typeof a.updated_at === "number" ? a.updated_at : Date.parse(a.updated_at) / 1000;
        const bv = typeof b.updated_at === "number" ? b.updated_at : Date.parse(b.updated_at) / 1000;
        return bv - av;
      })))
      .catch(() => setSessionsError(true));
  }, [sessionsRevision]);

  // Close context menu on outside click
  useEffect(() => {
    if (!menuId) return;
    const handler = () => setMenuId(null);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [menuId]);

  const handleRename = async (id: string) => {
    try {
      await patchSession(id, { title: renameValue.trim() || "Untitled" });
      setSessions((prev) =>
        prev.map((s) => s.id === id ? { ...s, title: renameValue.trim() || "Untitled" } : s)
      );
    } catch { /* ignore */ }
    setRenamingId(null);
    setMenuId(null);
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch { /* ignore */ }
    setMenuId(null);
  };

  const handleExport = async (id: string) => {
    setMenuId(null);
    try {
      const { markdown, filename } = await exportSession(id);
      const blob = new Blob([markdown], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Reset so the same file can be picked again.
    e.target.value = "";
    try {
      const text = await file.text();
      const result = await importSession(text);
      onSessionsRevisionBump();
      onSessionSelect(result.id);
    } catch { /* ignore */ }
  };

  return (
    <aside className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`}>
      {/* Top bar */}
      <div className="sidebar-top">
        {!collapsed && (
          <div className="sidebar-brand">
            <span className="sidebar-brand-dot" />
            <span className="sidebar-brand-name">Nexus</span>
          </div>
        )}
        <button
          className="sidebar-collapse-btn"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <IconCollapse collapsed={collapsed} />
        </button>
      </div>

      {/* New chat + Import */}
      <div className="sidebar-section">
        <div className={collapsed ? undefined : "sidebar-new-chat-row"}>
          <button className="sidebar-new-chat" onClick={onNewChat}>
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" />
              <line x1="4" y1="10" x2="16" y2="10" />
            </svg>
            {!collapsed && <span>New chat</span>}
          </button>
          {!collapsed && (
            <>
              <button
                className="sidebar-import-btn"
                title="Import session from .md file"
                onClick={() => importInputRef.current?.click()}
              >
                ↑ Import
              </button>
              <input
                ref={importInputRef}
                type="file"
                accept=".md,text/markdown"
                style={{ display: "none" }}
                onChange={(e) => void handleImportFile(e)}
              />
            </>
          )}
        </div>
      </div>

      {/* View switcher */}
      <div className="sidebar-section">
        {!collapsed && <div className="sidebar-section-label">Views</div>}
        <nav className="sidebar-nav">
          {VIEWS.map(({ id, label, Icon }) => (
            <button
              key={id}
              className={`sidebar-nav-item${view === id ? " sidebar-nav-item--active" : ""}`}
              onClick={() => onViewChange(id)}
              title={collapsed ? label : undefined}
            >
              <span className="sidebar-nav-icon"><Icon /></span>
              {!collapsed && <span className="sidebar-nav-label">{label}</span>}
            </button>
          ))}
        </nav>
      </div>

      {/* Sessions — only in Chat view */}
      {view === "chat" && !collapsed && (
        <div className="sidebar-section sidebar-sessions-section">
          <div className="sidebar-section-label">Sessions</div>
          {sessionsError && (
            <div className="sidebar-error">Couldn&apos;t load — is the server running?</div>
          )}
          <div className="sidebar-sessions">
            {sessions.map((s) => (
              <div
                key={s.id}
                className={`sidebar-session${s.id === activeSessionId ? " sidebar-session--active" : ""}`}
                onClick={() => onSessionSelect(s.id)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setMenuId(s.id);
                }}
              >
                {renamingId === s.id ? (
                  <input
                    className="sidebar-session-rename"
                    value={renameValue}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleRename(s.id);
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    onBlur={() => setRenamingId(null)}
                  />
                ) : (
                  <>
                    <span className="sidebar-session-title">{s.title || "Untitled"}</span>
                    <span className="sidebar-session-time">{fmtRelative(s.updated_at)}</span>
                  </>
                )}

                {menuId === s.id && (
                  <div
                    className="sidebar-context-menu"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      className="sidebar-ctx-item"
                      onClick={() => {
                        setRenamingId(s.id);
                        setRenameValue(s.title);
                        setMenuId(null);
                      }}
                    >
                      Rename
                    </button>
                    <button
                      className="sidebar-ctx-item"
                      onClick={() => void handleExport(s.id)}
                    >
                      Export...
                    </button>
                    <button
                      className="sidebar-ctx-item sidebar-ctx-item--danger"
                      onClick={() => void handleDelete(s.id)}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            ))}
            {sessions.length === 0 && !sessionsError && (
              <div className="sidebar-sessions-empty">No sessions yet</div>
            )}
          </div>
        </div>
      )}

      {/* Spacer */}
      <div className="sidebar-spacer" />

      {/* Settings */}
      <div className="sidebar-bottom">
        <button
          className="sidebar-nav-item"
          onClick={onOpenSettings}
          title={collapsed ? "Settings" : undefined}
        >
          <span className="sidebar-nav-icon"><IconGear /></span>
          {!collapsed && <span className="sidebar-nav-label">Settings</span>}
        </button>
      </div>
    </aside>
  );
}
