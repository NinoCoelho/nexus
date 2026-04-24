/**
 * Sidebar — the main navigation and session management panel.
 *
 * Layout (top to bottom):
 *   1. View switcher (Chat / Vault / Graph / Insights)
 *   2. Session list with search, rename, delete, export, import
 *   3. Vault file tree (expandable folders, file selection)
 *   4. Settings button
 *
 * All state flows down from App via props; the sidebar doesn't own any
 * session or chat state itself. Session mutations (rename, delete, etc.)
 * bump sessionsRevision so the list refreshes.
 */

import React, { useEffect, useRef, useState } from "react";
import { deleteSession, exportSession, getSessions, importSession, patchSession, searchSessions, sessionToVault, type SessionSearchResult, type SessionSummary } from "../api";
import { useToast } from "../toast/ToastProvider";
import VaultTreePanel from "./VaultTreePanel";
import "./Sidebar.css";

type View = "chat" | "vault" | "graph" | "insights";

interface Props {
  view: View;
  onViewChange: (v: View) => void;
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onNewChat: () => void;
  onOpenSettings: () => void;
  sessionsRevision: number;
  onSessionsRevisionBump: () => void;
  vaultSelectedPath: string | null;
  onVaultSelectPath: (path: string | null) => void;
  vaultOpenPath?: string | null;
  onVaultOpenPathHandled?: () => void;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onViewEntityGraph?: (mode: "file" | "folder", path: string) => void;
}

const SIDEBAR_WIDTH_KEY = "sidebar-width";
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 560;

function loadStoredWidth(): number {
  try {
    const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    if (!raw) return 220;
    const n = parseInt(raw, 10);
    if (!isFinite(n)) return 220;
    return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, n));
  } catch {
    return 220;
  }
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

function IconInsights() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 14 7 10 11 13 17 5" />
      <polyline points="13 5 17 5 17 9" />
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
  { id: "chat",       label: "Chat",        Icon: IconChat },
  { id: "vault",      label: "Vault",       Icon: IconVault },
  { id: "graph",      label: "Knowledge",   Icon: IconGraph },
  { id: "insights",   label: "Insights",    Icon: IconInsights },
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
  vaultSelectedPath,
  onVaultSelectPath,
  vaultOpenPath,
  onVaultOpenPathHandled,
  onDispatchToChat,
  onViewEntityGraph,
}: Props) {
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem("sidebar-collapsed") === "true"; }
    catch { return false; }
  });
  const [width, setWidth] = useState<number>(() => loadStoredWidth());
  const [resizing, setResizing] = useState(false);

  useEffect(() => {
    try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width)); } catch { /* ignore */ }
  }, [width]);

  const handleResizeStart = (e: React.MouseEvent) => {
    if (collapsed) return;
    e.preventDefault();
    setResizing(true);
    const startX = e.clientX;
    const startW = width;
    const onMove = (ev: MouseEvent) => {
      const next = Math.max(
        SIDEBAR_MIN_WIDTH,
        Math.min(SIDEBAR_MAX_WIDTH, startW + (ev.clientX - startX)),
      );
      setWidth(next);
    };
    const onUp = () => {
      setResizing(false);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsError, setSessionsError] = useState(false);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([]);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Rename inline state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // Context menu — tracks the id + the anchor rect so we can render as a
  // position:fixed popover outside the row's overflow-hidden clip.
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const menuId = menu?.id ?? null;
  const setMenuId = (id: string | null) => {
    if (id == null) setMenu(null);
  };
  /** ids currently sending to the vault ("summary" mode can take seconds) */
  const [toVaultBusy, setToVaultBusy] = useState<Set<string>>(new Set());

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

  // Debounced search
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (!searchQuery.trim()) {
      setSearchResults([]);
      return;
    }
    searchTimerRef.current = setTimeout(() => {
      searchSessions(searchQuery)
        .then(setSearchResults)
        .catch(() => setSearchResults([]));
    }, 300);
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [searchQuery]);

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
      toast.success(`Downloaded ${filename}`);
    } catch (e) {
      toast.error("Download failed", { detail: e instanceof Error ? e.message : undefined });
    }
  };

  const handleToVault = async (id: string, mode: "raw" | "summary") => {
    setMenuId(null);
    setToVaultBusy((prev) => new Set(prev).add(id));
    if (mode === "summary") {
      toast.info("Summarizing session…", { duration: 2500 });
    }
    try {
      const result = await sessionToVault(id, mode);
      toast.success(
        mode === "raw" ? "Saved raw to vault" : "Summary saved to vault",
        { detail: result.path, duration: 5000 },
      );
    } catch (e) {
      toast.error(
        mode === "raw" ? "Couldn't save raw to vault" : "Summarize failed",
        { detail: e instanceof Error ? e.message : undefined },
      );
    } finally {
      setToVaultBusy((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
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
    <aside
      className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`}
      style={collapsed ? undefined : ({ ["--sidebar-width" as unknown as string]: `${width}px` } as React.CSSProperties)}
    >
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

          {/* Search input */}
          <div className="sidebar-search-wrap">
            <input
              className="sidebar-search-input"
              type="search"
              placeholder="Search messages…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search session messages"
            />
          </div>

          {/* Search results dropdown */}
          {searchQuery.trim() && (
            <div className="sidebar-search-results">
              {searchResults.length === 0 ? (
                <div className="sidebar-search-empty">No results</div>
              ) : (
                searchResults.map((r) => (
                  <button
                    key={`${r.session_id}-${r.snippet}`}
                    className="sidebar-search-result"
                    onClick={() => {
                      onSessionSelect(r.session_id);
                      setSearchQuery("");
                      setSearchResults([]);
                    }}
                  >
                    <span className="sidebar-search-result-title">{r.title}</span>
                    <span
                      className="sidebar-search-result-snippet"
                      // snippet may contain **bold** markers from FTS5 — render as-is
                      dangerouslySetInnerHTML={{
                        __html: r.snippet.replace(
                          /\*\*(.*?)\*\*/g,
                          "<strong>$1</strong>",
                        ),
                      }}
                    />
                  </button>
                ))
              )}
            </div>
          )}

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
                  setMenu({ id: s.id, x: e.clientX, y: e.clientY });
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
                    <span
                      className="sidebar-session-title"
                      onDoubleClick={(e) => {
                        e.stopPropagation();
                        setRenamingId(s.id);
                        setRenameValue(s.title || "");
                      }}
                      title="Double-click to rename"
                    >
                      {s.title || "Untitled"}
                      {toVaultBusy.has(s.id) && " ⋯"}
                    </span>
                    <span className="sidebar-session-time">{fmtRelative(s.updated_at)}</span>
                    <button
                      className="sidebar-session-menu-btn"
                      aria-label="Session actions"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (menu?.id === s.id) {
                          setMenu(null);
                        } else {
                          const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                          setMenu({ id: s.id, x: r.right + 4, y: r.top });
                        }
                      }}
                      title="More actions"
                    >
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                        <circle cx="3" cy="8" r="1.3" />
                        <circle cx="8" cy="8" r="1.3" />
                        <circle cx="13" cy="8" r="1.3" />
                      </svg>
                    </button>
                  </>
                )}
              </div>
            ))}
            {sessions.length === 0 && !sessionsError && (
              <div className="sidebar-sessions-empty">No sessions yet</div>
            )}
          </div>
        </div>
      )}

      {/* Vault tree — only in Vault view */}
      {view === "vault" && !collapsed && (
        <div className="sidebar-section sidebar-vault-section">
          <VaultTreePanel
            selectedPath={vaultSelectedPath}
            onSelectPath={onVaultSelectPath}
            openPath={vaultOpenPath}
            onOpenPathHandled={onVaultOpenPathHandled}
            onDispatchToChat={onDispatchToChat}
            onViewEntityGraph={onViewEntityGraph}
          />
        </div>
      )}

      {/* Spacer — only when no expandable section is active */}
      {!(view === "chat" && !collapsed) && !(view === "vault" && !collapsed) && (
        <div className="sidebar-spacer" />
      )}

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

      {/* Floating context menu — position:fixed so it escapes the row's
          overflow:hidden clip. Anchored to the cursor (right-click) or
          the ⋮ button's rect (left-click). */}
      {menu && (() => {
        const s = sessions.find((x) => x.id === menu.id);
        if (!s) return null;
        // Keep the menu on-screen: if the anchor is too close to the right
        // edge, flip to the left of the cursor/button.
        const width = 200;
        const left = Math.min(menu.x, window.innerWidth - width - 8);
        const top = Math.min(menu.y, window.innerHeight - 240);
        return (
          <div
            className="sidebar-context-menu sidebar-context-menu--floating"
            style={{ top, left, width }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="sidebar-ctx-item"
              onClick={() => {
                setRenamingId(s.id);
                setRenameValue(s.title);
                setMenu(null);
              }}
            >
              Rename
            </button>
            <button
              className="sidebar-ctx-item"
              onClick={() => void handleExport(s.id)}
            >
              Download .md
            </button>
            <div className="sidebar-ctx-divider" />
            <button
              className="sidebar-ctx-item"
              disabled={toVaultBusy.has(s.id)}
              onClick={() => void handleToVault(s.id, "raw")}
              title="Save the full transcript to the vault"
            >
              Send to vault (raw)
            </button>
            <button
              className="sidebar-ctx-item"
              disabled={toVaultBusy.has(s.id)}
              onClick={() => void handleToVault(s.id, "summary")}
              title="Have Nexus summarize this session and save the note"
            >
              Send to vault (summary)
            </button>
            <div className="sidebar-ctx-divider" />
            <button
              className="sidebar-ctx-item sidebar-ctx-item--danger"
              onClick={() => void handleDelete(s.id)}
            >
              Delete
            </button>
          </div>
        );
      })()}

      {!collapsed && (
        <div
          className={`sidebar-resize-handle${resizing ? " sidebar-resize-handle--active" : ""}`}
          onMouseDown={handleResizeStart}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          title="Drag to resize"
        />
      )}
    </aside>
  );
}
