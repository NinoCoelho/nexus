/**
 * Sidebar — main nav/session panel. State flows from App via props;
 * session mutations bump sessionsRevision to refresh the list.
 *
 * Nav groups: primary (Chat, Projects) and content (Apps, Vault, Calendar,
 * Workflows). Advanced views (knowledge/heartbeat/dream) live behind
 * #/advanced/* routes and the Settings drawer — not here.
 */

import React, { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getSessions, searchSessions,
  type SessionSearchResult, type SessionSummary,
  type SessionsResponse,
} from "../../api";
import { listProjects, type ProjectSummary } from "../../api/projects";
import { useToast } from "../../toast/ToastProvider";
import { checkUpdate as apiCheckUpdate, type UpdateCheckResult } from "../../api/update";
import VaultTreePanel from "../VaultTreePanel";
import WorkflowListPanel from "../WorkflowListPanel";
import ProjectsListPanel from "./ProjectsListPanel";
import AppsListPanel from "./AppsListPanel";
import { IconChat, IconCalendar, IconVault, IconWorkflow, IconGear, IconCollapse, IconDatabase, IconProjects, IconUpdate } from "./icons";
import SessionsPanel from "./SessionsPanel";
import PinnedPanel from "./PinnedPanel";
import SessionContextMenu from "./SessionContextMenu";
import ProjectCreateModal from "./ProjectCreateModal";
import ProjectEditModal from "./ProjectEditModal";
import ProjectContextMenu from "./ProjectContextMenu";
import { loadStoredWidth, SIDEBAR_WIDTH_KEY, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH } from "./utils";
import { useSessionActions } from "./useSessionActions";
import { BrandMark } from "../BrandMark";
import type { AnyView } from "../../routes";
import "../Sidebar.css";

interface Props {
  view: AnyView;
  onViewChange: (v: AnyView) => void;
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onNewChat: (projectId?: string | null) => void;
  onOpenSettings: () => void;
  sessionsRevision: number;
  onSessionsRevisionBump: () => void;
  /** Optimistic placeholder shown above fetched sessions while the first
   * turn is in flight — lets the user see their new chat immediately. */
  pendingNewSession?: SessionSummary | null;
  /** Called after the *active* session is deleted — host clears the chat surface. */
  onActiveSessionDeleted?: () => void;
  vaultSelectedPath: string | null;
  onVaultSelectPath: (path: string | null) => void;
  vaultOpenPath?: string | null;
  onVaultOpenPathHandled?: () => void;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onViewEntityGraph?: (mode: "file" | "folder", path: string) => void;
  onVisualizeFolderGraph?: (path: string) => void;
  /** Mobile drawer open state. When true, sidebar slides in from the left. */
  mobileOpen?: boolean;
  onMobileClose?: () => void;
  onUpdateAvailable?: (check: UpdateCheckResult) => void;
  /** Coordinator master session id — badges the row in the session list. */
  coordinatorSessionId?: string | null;
  /** Selected project in the Projects view (shared with ProjectsPane). */
  projectsSelectedId?: string | null;
  onProjectsSelect?: (id: string) => void;
  /** Selected app folder in the Apps view (shared with AppsPane). */
  appSelectedFolder?: string | null;
  onAppSelectFolder?: (folder: string | null) => void;
}

function Sidebar({
  view, onViewChange, activeSessionId, onSessionSelect, onNewChat, onOpenSettings,
  sessionsRevision, onSessionsRevisionBump, pendingNewSession, onActiveSessionDeleted, vaultSelectedPath, onVaultSelectPath,
  vaultOpenPath, onVaultOpenPathHandled, onDispatchToChat, onViewEntityGraph,
  onVisualizeFolderGraph,
  mobileOpen = false, onMobileClose,
  onUpdateAvailable,
  coordinatorSessionId = null,
  projectsSelectedId = null,
  onProjectsSelect,
  appSelectedFolder = null,
  onAppSelectFolder,
}: Props) {
  const { t } = useTranslation("sidebar");
  const NAV_GROUPS = {
    primary: [
      { id: "chat" as const,     label: t("sidebar:viewNames.chat"),     Icon: IconChat },
      { id: "projects" as const, label: t("sidebar:viewNames.projects"), Icon: IconProjects },
    ],
    content: [
      { id: "apps" as const,     label: t("sidebar:viewNames.apps"),     Icon: IconDatabase },
      { id: "vault" as const,    label: t("sidebar:viewNames.vault"),    Icon: IconVault },
      { id: "calendar" as const, label: t("sidebar:viewNames.calendar"), Icon: IconCalendar },
      { id: "workflows" as const, label: "Workflows", Icon: IconWorkflow },
    ],
  };
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem("sidebar-collapsed") === "true"; }
    catch { return false; }
  });
  const [width, setWidth] = useState<number>(() => loadStoredWidth());
  const [resizing, setResizing] = useState(false);

  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckResult | null>(null);
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      apiCheckUpdate().then((r) => {
        if (!cancelled && r.update_available) {
          setUpdateAvailable(true);
          setUpdateCheck(r);
          onUpdateAvailable?.(r);
        }
      }).catch(() => {});
    }, 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

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
  const [sessionsOffset, setSessionsOffset] = useState(0);
  const [sessionsTotal, setSessionsTotal] = useState(0);
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

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [editProjectId, setEditProjectId] = useState<string | null>(null);
  const [projectMenu, setProjectMenu] = useState<{ id: string; x: number; y: number } | null>(null);

  const refreshProjects = () => {
    listProjects().then(setProjects).catch(() => {});
  };

  useEffect(() => {
    refreshProjects();
  }, [sessionsRevision]);

  const SESSIONS_PAGE = 20;

  useEffect(() => {
    setSessionsError(false);
    getSessions(SESSIONS_PAGE, 0)
      .then(({ sessions: s, total }: SessionsResponse) => {
        setSessions(s.sort((a, b) => {
          const av = typeof a.updated_at === "number" ? a.updated_at : Date.parse(a.updated_at) / 1000;
          const bv = typeof b.updated_at === "number" ? b.updated_at : Date.parse(b.updated_at) / 1000;
          return bv - av;
        }));
        setSessionsOffset(SESSIONS_PAGE);
        setSessionsTotal(total);
      })
      .catch(() => setSessionsError(true));
  }, [sessionsRevision]);

  // "Has more" = more *ungrouped* pages exist. The endpoint returns ALL
  // project sessions on every call and X-Total-Count counts ungrouped
  // sessions only, so compare the ungrouped slice against that total.
  const hasMoreSessions = sessions.filter((s) => !s.project_id).length < sessionsTotal;

  const handleLoadMoreSessions = () => {
    getSessions(SESSIONS_PAGE, sessionsOffset)
      .then(({ sessions: newBatch, total }: SessionsResponse) => {
        setSessions((prev) => {
          const existing = new Set(prev.map((s) => s.id));
          const added = newBatch.filter((s) => !existing.has(s.id));
          return [...prev, ...added].sort((a, b) => {
            const av = typeof a.updated_at === "number" ? a.updated_at : Date.parse(a.updated_at) / 1000;
            const bv = typeof b.updated_at === "number" ? b.updated_at : Date.parse(b.updated_at) / 1000;
            return bv - av;
          });
        });
        setSessionsOffset((o) => o + SESSIONS_PAGE);
        setSessionsTotal(total);
      })
      .catch(() => {});
  };

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

  // Close project context menu on outside click
  useEffect(() => {
    if (!projectMenu) return;
    const handler = () => setProjectMenu(null);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [projectMenu]);

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
    activeSessionId,
    onActiveSessionDeleted: onActiveSessionDeleted ?? (() => {}),
    toast,
  });

  // Merge the optimistic placeholder above fetched sessions, but only while
  // the real entry hasn't arrived yet — once the backend lists it, the real
  // row takes over.
  const displaySessions = pendingNewSession && !sessions.some((s) => s.id === pendingNewSession.id)
    ? [pendingNewSession, ...sessions]
    : sessions;

  const renderNavItems = (items: ReadonlyArray<{ id: AnyView; label: string; Icon: React.ComponentType }>) =>
    items.map(({ id, label, Icon }) => (
      <button
        key={id}
        className={`sidebar-nav-item${view === id ? " sidebar-nav-item--active" : ""}`}
        onClick={() => onViewChange(id)}
        title={collapsed ? label : undefined}
      >
        <span className="sidebar-nav-icon"><Icon /></span>
        {!collapsed && <span className="sidebar-nav-label">{label}</span>}
      </button>
    ));

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
            <BrandMark size="sm" />
          </div>
        )}
        <button
          className="sidebar-collapse-btn"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? t("sidebar:expand") : t("sidebar:collapse")}
          aria-label={collapsed ? t("sidebar:expand") : t("sidebar:collapse")}
        >
          <IconCollapse collapsed={collapsed} />
        </button>
      </div>

      {/* New chat + Import */}
      <div className="sidebar-section">
        <div className={collapsed ? undefined : "sidebar-new-chat-row"}>
          <button className="sidebar-new-chat" onClick={() => onNewChat()}>
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" />
              <line x1="4" y1="10" x2="16" y2="10" />
            </svg>
            {!collapsed && <span>{t("sidebar:newChat")}</span>}
          </button>
          {!collapsed && (
            <>
              <button className="sidebar-import-btn" title={t("sidebar:importSession")} onClick={() => importInputRef.current?.click()}>
                {t("sidebar:importLabel")}
              </button>
              <input ref={importInputRef} type="file" accept=".md,text/markdown" style={{ display: "none" }} onChange={(e) => void sessionActions.handleImportFile(e)} />
            </>
          )}
        </div>
      </div>

      {/* View switcher */}
      <div className="sidebar-section">
        {!collapsed && <div className="sidebar-section-label">{t("sidebar:views")}</div>}
        <nav className="sidebar-nav">
          {renderNavItems(NAV_GROUPS.primary)}
          {!collapsed && <div className="sidebar-nav-divider" />}
          {renderNavItems(NAV_GROUPS.content)}
        </nav>
      </div>

      {/* Pinned + Sessions — only in Chat view */}
      {view === "chat" && !collapsed && (
        <PinnedPanel refreshKey={sessionsRevision} onOpenSession={onSessionSelect} />
      )}
      {view === "chat" && !collapsed && (
        <SessionsPanel
          sessions={displaySessions}
          projects={projects}
          sessionsError={sessionsError}
          activeSessionId={activeSessionId}
          searchQuery={searchQuery}
          searchResults={searchResults}
          renamingId={renamingId}
          renameValue={renameValue}
          toVaultBusy={toVaultBusy}
          canCreateProject
          hasMore={hasMoreSessions}
          masterSessionId={coordinatorSessionId}
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
          onNewProject={() => setShowCreateProject(true)}
          onProjectContextMenu={(e, projectId) => {
            e.preventDefault();
            setProjectMenu({ id: projectId, x: e.clientX, y: e.clientY });
          }}
          onNewChatInProject={(projectId) => onNewChat(projectId)}
          onLoadMore={handleLoadMoreSessions}
        />
      )}

      {/* Projects list — only in Projects view */}
      {view === "projects" && !collapsed && onProjectsSelect && (
        <ProjectsListPanel
          selectedId={projectsSelectedId}
          onSelect={onProjectsSelect}
          onNewProject={() => setShowCreateProject(true)}
          refreshKey={sessionsRevision}
        />
      )}

      {/* Apps list — only in Apps view */}
      {view === "apps" && !collapsed && onAppSelectFolder && (
        <AppsListPanel
          selectedFolder={appSelectedFolder}
          onSelect={onAppSelectFolder}
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
            onVisualizeFolderGraph={onVisualizeFolderGraph}
          />
        </div>
      )}

      {/* Workflow list — only in Workflows view */}
      {view === "workflows" && !collapsed && (
        <div className="sidebar-section sidebar-vault-section">
          <WorkflowListPanel
            selectedPath={vaultSelectedPath}
            onOpen={(p) => { onVaultSelectPath(p); onViewChange("workflows"); }}
          />
        </div>
      )}

      {/* Spacer — only when no expandable section is active */}
      {!(view === "chat" && !collapsed) &&
        !(view === "projects" && !collapsed) &&
        !(view === "apps" && !collapsed) &&
        !(view === "vault" && !collapsed) &&
        !(view === "workflows" && !collapsed) && (
        <div className="sidebar-spacer" />
      )}

      {/* Settings */}
      <div className="sidebar-bottom">
        {updateAvailable && updateCheck && (
          <button
            className="sidebar-nav-item sidebar-update-btn"
            onClick={() => onUpdateAvailable?.(updateCheck)}
            title={collapsed ? `Update available: v${updateCheck.latest}` : undefined}
          >
            <span className="sidebar-nav-icon sidebar-update-icon">
              <IconUpdate />
              <span className="sidebar-update-dot" />
            </span>
            {!collapsed && <span className="sidebar-nav-label">Update v{updateCheck.latest}</span>}
          </button>
        )}
        <button className="sidebar-nav-item" onClick={onOpenSettings} title={collapsed ? t("sidebar:settings") : undefined}>
          <span className="sidebar-nav-icon"><IconGear /></span>
          {!collapsed && <span className="sidebar-nav-label">{t("sidebar:settings")}</span>}
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
            projects={projects}
            onRename={() => { setRenamingId(s.id); setRenameValue(s.title); setMenu(null); }}
            onExport={() => void sessionActions.handleExport(s.id)}
            onToVaultRaw={() => void sessionActions.handleToVault(s.id, "raw")}
            onToVaultSummary={() => void sessionActions.handleToVault(s.id, "summary")}
            onDelete={() => void sessionActions.handleDelete(s.id)}
            onClick={(e) => e.stopPropagation()}
            onMoveToProject={async (projectId) => {
              setMenu(null);
              try {
                const { moveSessionToProject } = await import("../../api/projects");
                await moveSessionToProject(projectId, s.id);
                onSessionsRevisionBump();
              } catch {}
            }}
            onRemoveFromProject={s.project_id ? async () => {
              setMenu(null);
              try {
                const { removeSessionFromProject } = await import("../../api/projects");
                await removeSessionFromProject(s.project_id!, s.id);
                onSessionsRevisionBump();
              } catch {}
            } : undefined}
          />
        );
      })()}

      {projectMenu && (() => {
        const p = projects.find((x) => x.id === projectMenu.id);
        if (!p) return null;
        return (
          <ProjectContextMenu
            project={p}
            anchorX={projectMenu.x}
            anchorY={projectMenu.y}
            onEdit={() => { setEditProjectId(p.id); setProjectMenu(null); }}
            onDelete={async () => {
              const { deleteProject } = await import("../../api/projects");
              deleteProject(p.id).then(() => {
                refreshProjects();
                onSessionsRevisionBump();
              }).catch(() => {});
              setProjectMenu(null);
            }}
            onClick={(e) => e.stopPropagation()}
          />
        );
      })()}

      <ProjectCreateModal
        open={showCreateProject}
        onClose={() => setShowCreateProject(false)}
        onCreated={() => { refreshProjects(); onSessionsRevisionBump(); }}
      />

      <ProjectEditModal
        open={editProjectId !== null}
        projectId={editProjectId}
        onClose={() => setEditProjectId(null)}
        onSaved={() => { refreshProjects(); onSessionsRevisionBump(); }}
      />

      {!collapsed && (
        <div
          className={`sidebar-resize-handle${resizing ? " sidebar-resize-handle--active" : ""}`}
          onMouseDown={handleResizeStart}
          role="separator"
          aria-orientation="vertical"
          aria-label={t("sidebar:resize")}
          title={t("sidebar:resizeDrag")}
        />
      )}
    </aside>
    </>
  );
}

export default memo(Sidebar);
