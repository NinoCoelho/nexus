/**
 * Sidebar — main nav/session panel. State flows from App via props;
 * session mutations bump sessionsRevision to refresh the list.
 */

import React, { useEffect, useRef, useState } from "react";
import {
  getSessions, searchSessions,
  type SessionSearchResult, type SessionSummary,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import VaultTreePanel from "../VaultTreePanel";
import { IconChat, IconCalendar, IconVault, IconGraph, IconInsights, IconGear, IconCollapse } from "./icons";
import SessionsPanel from "./SessionsPanel";
import PinnedPanel from "./PinnedPanel";
import SessionContextMenu from "./SessionContextMenu";
import { loadStoredWidth, SIDEBAR_WIDTH_KEY, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH } from "./utils";
import { useSessionActions } from "./useSessionActions";
import "../Sidebar.css";

type View = "chat" | "calendar" | "vault" | "graph" | "insights";

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
  /** Mobile drawer open state. When true, sidebar slides in from the left. */
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

const VIEWS: { id: View; label: string; Icon: () => React.ReactElement }[] = [
  { id: "chat",     label: "Chat",      Icon: IconChat },
  { id: "calendar", label: "Calendar",  Icon: IconCalendar },
  { id: "vault",    label: "Vault",     Icon: IconVault },
  { id: "graph",    label: "Knowledge", Icon: IconGraph },
  { id: "insights", label: "Insights",  Icon: IconInsights },
];

export default function Sidebar({
  view, onViewChange, activeSessionId, onSessionSelect, onNewChat, onOpenSettings,
  sessionsRevision, onSessionsRevisionBump, vaultSelectedPath, onVaultSelectPath,
  vaultOpenPath, onVaultOpenPathHandled, onDispatchToChat, onViewEntityGraph,
  mobileOpen = false, onMobileClose,
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
  useEffect(() => { localStorage.setItem("sidebar-collapsed", String(collapsed)); }, [collapsed]);

  const handleResizeStart = (e: React.MouseEvent) => {
    if (collapsed) return;
    e.preventDefault();
    setResizing(true);
    const startX = e.clientX;
    const startW = width;
    const onMove = (ev: MouseEvent) => {
      const next = Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, startW + (ev.clientX - startX)));
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
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([]);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Context menu — tracks the id + the anchor rect so we can render as a
  // position:fixed popover outside the row's overflow-hidden clip.
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const menuId = menu?.id ?? null;
  const setMenuNull = () => setMenu(null);
  /** ids currently sending to the vault ("summary" mode can take seconds) */
  const [toVaultBusy, setToVaultBusy] = useState<Set<string>>(new Set());
  const importInputRef = useRef<HTMLInputElement>(null);

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
    if (!searchQuery.trim()) { setSearchResults([]); return; }
    searchTimerRef.current = setTimeout(() => {
      searchSessions(searchQuery).then(setSearchResults).catch(() => setSearchResults([]));
    }, 300);
    return () => { if (searchTimerRef.current) clearTimeout(searchTimerRef.current); };
  }, [searchQuery]);

  // Close context menu on outside click
  useEffect(() => {
    if (!menuId) return;
    const handler = () => setMenuNull();
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [menuId]);

  const sessionActions = useSessionActions({
    sessions,
    setSessions,
    renamingId,
    renameValue,
    setRenamingId,
    setMenuNull,
    setToVaultBusy,
    onSessionsRevisionBump,
    onSessionSelect,
    toast,
  });

  return (
    <>
      {mobileOpen && (
        <div
          className="sidebar-backdrop mobile-only"
          onClick={onMobileClose}
          aria-hidden="true"
        />
      )}
    <aside
      className={`sidebar${collapsed ? " sidebar--collapsed" : ""}${mobileOpen ? " sidebar--mobile-open" : ""}`}
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
              <button className="sidebar-import-btn" title="Import session from .md file" onClick={() => importInputRef.current?.click()}>
                ↑ Import
              </button>
              <input ref={importInputRef} type="file" accept=".md,text/markdown" style={{ display: "none" }} onChange={(e) => void sessionActions.handleImportFile(e)} />
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

      {/* Pinned + Sessions — only in Chat view */}
      {view === "chat" && !collapsed && (
        <PinnedPanel refreshKey={sessionsRevision} onOpenSession={onSessionSelect} />
      )}
      {view === "chat" && !collapsed && (
        <SessionsPanel
          sessions={sessions}
          sessionsError={sessionsError}
          activeSessionId={activeSessionId}
          searchQuery={searchQuery}
          searchResults={searchResults}
          renamingId={renamingId}
          renameValue={renameValue}
          toVaultBusy={toVaultBusy}
          menuId={menuId}
          onSearchChange={(q) => { setSearchQuery(q); if (!q) setSearchResults([]); }}
          onSessionSelect={onSessionSelect}
          onContextMenu={(e, id) => { e.preventDefault(); setMenu({ id, x: e.clientX, y: e.clientY }); }}
          onMenuBtnClick={(e, id) => {
            e.stopPropagation();
            if (menu?.id === id) { setMenu(null); }
            else { const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); setMenu({ id, x: r.right + 4, y: r.top }); }
          }}
          onTitleDoubleClick={(e, id, title) => { e.stopPropagation(); setRenamingId(id); setRenameValue(title); }}
          onRenameChange={setRenameValue}
          onRenameCommit={(id) => void sessionActions.handleRename(id)}
          onRenameCancel={() => setRenamingId(null)}
        />
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
        <button className="sidebar-nav-item" onClick={onOpenSettings} title={collapsed ? "Settings" : undefined}>
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
        return (
          <SessionContextMenu
            session={s}
            anchorX={menu.x}
            anchorY={menu.y}
            toVaultBusy={toVaultBusy}
            onRename={() => { setRenamingId(s.id); setRenameValue(s.title); setMenu(null); }}
            onExport={() => void sessionActions.handleExport(s.id)}
            onToVaultRaw={() => void sessionActions.handleToVault(s.id, "raw")}
            onToVaultSummary={() => void sessionActions.handleToVault(s.id, "summary")}
            onShare={() => void sessionActions.handleShare(s.id)}
            onDelete={() => void sessionActions.handleDelete(s.id)}
            onClick={(e) => e.stopPropagation()}
          />
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
    </>
  );
}
