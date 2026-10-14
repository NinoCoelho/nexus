/**
 * Sidebar — main nav panel. State flows from App via props; session
 * mutations bump sessionsRevision to refresh the list.
 *
 * Layout: an always-visible Bots list (coordinator "main" + every bot,
 * each row opening its chat or workspace) above the content nav (Apps,
 * Vault, Calendar, Kanban, Workflows). The per-view list panels (vault
 * tree, app databases, boards, calendars, workflow files, pinned chats)
 * render below the nav for the active view. Advanced views (knowledge/
 * heartbeat/dream) live behind #/advanced/* routes and the Settings
 * drawer — not here.
 */

import React, { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  archiveSession,
  getSessions,
  type Bot,
  type SessionSummary,
  type SessionsResponse,
} from "../../api";
import { listProjects, type ProjectSummary } from "../../api/projects";
import { useToast } from "../../toast/ToastProvider";
import { checkUpdate as apiCheckUpdate, type UpdateCheckResult } from "../../api/update";
import VaultTreePanel from "../VaultTreePanel";
import WorkflowListPanel from "../WorkflowListPanel";
import BotsListPanel from "./BotsListPanel";
import KanbanListPanel from "./KanbanListPanel";
import CalendarListPanel from "./CalendarListPanel";
import AppsListPanel from "./AppsListPanel";
import { IconCalendar, IconVault, IconKanban, IconWorkflow, IconCollapse, IconDatabase, IconUpdate } from "./icons";
import PinnedPanel from "./PinnedPanel";
import SessionContextMenu from "./SessionContextMenu";
import ProjectCreateModal from "./ProjectCreateModal";
import TeamModal from "./TeamModal";
import DeleteBotModal from "./DeleteBotModal";
import { loadStoredWidth, loadViewsCollapsed, SIDEBAR_WIDTH_KEY, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH, VIEWS_COLLAPSED_KEY } from "./utils";
import { useSessionActions } from "./useSessionActions";
import { BrandMark } from "../BrandMark";
import type { AnyView } from "../../routes";
import "../Sidebar.css";

interface Props {
  view: AnyView;
  onViewChange: (v: AnyView) => void;
  activeSessionId: string | null;
  onSessionSelect: (id: string, projectId?: string | null) => void;
  onNewChat: (projectId?: string | null) => void;
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
  /** Coordinator master session id — badges its row in the bots list. */
  coordinatorSessionId?: string | null;
  /** Coordinator display name (badges + synthesized main row). */
  coordinatorName?: string | null;
  /** Collapsed (icon-rail) state — owned by App so the Header's maximize
   * button and the sidebar's own toggle stay in sync. */
  collapsed: boolean;
  onCollapsedChange: (v: boolean) => void;
  /** Selected bot in the Bots view (shared with BotsPane). */
  botsSelectedId?: string | null;
  /** Opens a bot's workspace in the Bots view. */
  onOpenBot?: (id: string) => void;
  /** Selected app folder in the Apps view (shared with AppsPane). */
  appSelectedFolder?: string | null;
  onAppSelectFolder?: (folder: string | null) => void;
  /** Selected kanban board path (shared with the Kanban pane). */
  kanbanSelectedPath?: string | null;
  onKanbanOpen?: (path: string) => void;
  /** Selected calendar path (shared with the Calendar view). */
  calendarSelectedPath?: string | null;
  onCalendarSelect?: (path: string) => void;
}

function Sidebar({
  view, onViewChange, activeSessionId, onSessionSelect, onNewChat,
  sessionsRevision, onSessionsRevisionBump, pendingNewSession, onActiveSessionDeleted, vaultSelectedPath, onVaultSelectPath,
  vaultOpenPath, onVaultOpenPathHandled, onDispatchToChat, onViewEntityGraph,
  onVisualizeFolderGraph,
  mobileOpen = false, onMobileClose,
  onUpdateAvailable,
  coordinatorSessionId = null,
  coordinatorName = null,
  collapsed,
  onCollapsedChange,
  botsSelectedId = null,
  onOpenBot,
  appSelectedFolder = null,
  onAppSelectFolder,
  kanbanSelectedPath = null,
  onKanbanOpen,
  calendarSelectedPath = null,
  onCalendarSelect,
}: Props) {
  const { t } = useTranslation("sidebar");
  const NAV_ITEMS = [
    { id: "apps" as const,     label: t("sidebar:viewNames.apps"),     Icon: IconDatabase },
    { id: "vault" as const,    label: t("sidebar:viewNames.vault"),    Icon: IconVault },
    { id: "calendar" as const, label: t("sidebar:viewNames.calendar"), Icon: IconCalendar },
    { id: "kanban" as const,   label: t("sidebar:viewNames.kanban"),   Icon: IconKanban },
    { id: "workflows" as const, label: "Workflows",                    Icon: IconWorkflow },
  ];
  const toast = useToast();
  const setCollapsed = onCollapsedChange;
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
  const [showTeamModal, setShowTeamModal] = useState(false);
  const [deleteBotTarget, setDeleteBotTarget] = useState<Bot | null>(null);
  const [viewsCollapsed, setViewsCollapsed] = useState(loadViewsCollapsed);

  const refreshProjects = () => {
    listProjects().then(setProjects).catch(() => {});
  };

  useEffect(() => {
    refreshProjects();
  }, [sessionsRevision]);

  const SESSIONS_PAGE = 20;

  useEffect(() => {
    getSessions(SESSIONS_PAGE, 0)
      .then(({ sessions: s }: SessionsResponse) => {
        setSessions(s.sort((a, b) => {
          const av = typeof a.updated_at === "number" ? a.updated_at : Date.parse(a.updated_at) / 1000;
          const bv = typeof b.updated_at === "number" ? b.updated_at : Date.parse(b.updated_at) / 1000;
          return bv - av;
        }));
      })
      .catch(() => {});
  }, [sessionsRevision]);

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

  const lowerPanelActive =
    (view === "bots") ||
    (view === "apps" && !collapsed && !!onAppSelectFolder) ||
    (view === "vault" && !collapsed) ||
    (view === "calendar" && !collapsed) ||
    (view === "kanban" && !collapsed) ||
    (view === "workflows" && !collapsed);

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
          onClick={() => setCollapsed(!collapsed)}
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

      {/* Bots list — always visible, scrolls independently when long */}
      {!collapsed && onOpenBot && (
        <BotsListPanel
          refreshKey={sessionsRevision}
          sessions={displaySessions}
          activeSessionId={activeSessionId}
          selectedBotId={view === "bots" ? botsSelectedId : null}
          coordinatorSessionId={coordinatorSessionId}
          coordinatorName={coordinatorName}
          capped={lowerPanelActive}
          onSessionSelect={onSessionSelect}
          onNewChat={onNewChat}
          onOpenBot={onOpenBot}
          onNewBot={() => setShowCreateProject(true)}
          onNewTeam={() => setShowTeamModal(true)}
          onDeleteBot={(bot) => setDeleteBotTarget(bot)}
          renamingId={renamingId}
          renameValue={renameValue}
          toVaultBusy={toVaultBusy}
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

      {/* View switcher */}
      <div className={`sidebar-section${lowerPanelActive && !collapsed ? " sidebar-section--with-bots" : ""}`}>
        {!collapsed ? (
          <button
            className="sidebar-section-head"
            onClick={() => {
              setViewsCollapsed((v) => {
                try { localStorage.setItem(VIEWS_COLLAPSED_KEY, v ? "0" : "1"); } catch { /* ignore */ }
                return !v;
              });
            }}
            aria-expanded={!viewsCollapsed}
            title={viewsCollapsed ? "Expand views" : "Collapse views"}
          >
            <span className="sidebar-section-label">{t("sidebar:views")}</span>
            <svg
              className={`sidebar-collapse-chevron${viewsCollapsed ? "" : " sidebar-collapse-chevron--open"}`}
              width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M6 4l4 4-4 4" />
            </svg>
          </button>
        ) : null}
        <nav className="sidebar-nav" style={viewsCollapsed && !collapsed ? { display: "none" } : undefined}>
          {renderNavItems(NAV_ITEMS)}
        </nav>
      </div>

      {/* Pinned — only in Bots view */}
      {view === "bots" && !collapsed && (
        <PinnedPanel refreshKey={sessionsRevision} onOpenSession={onSessionSelect} />
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

      {/* Calendars — only in Calendar view */}
      {view === "calendar" && !collapsed && onCalendarSelect && (
        <CalendarListPanel
          selectedPath={calendarSelectedPath}
          onSelect={onCalendarSelect}
          refreshKey={sessionsRevision}
        />
      )}

      {/* Kanban boards — only in Kanban view */}
      {view === "kanban" && !collapsed && onKanbanOpen && (
        <KanbanListPanel selectedPath={kanbanSelectedPath} onOpen={onKanbanOpen} />
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
      {!(view === "bots" && !collapsed) &&
        !(view === "apps" && !collapsed) &&
        !(view === "vault" && !collapsed) &&
        !(view === "calendar" && !collapsed) &&
        !(view === "kanban" && !collapsed) &&
        !(view === "workflows" && !collapsed) && (
        <div className="sidebar-spacer" />
      )}

      {/* Update notice — pinned at the bottom; Settings lives in the Header gear */}
      {updateAvailable && updateCheck && (
        <div className="sidebar-bottom">
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
        </div>
      )}

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
            onArchive={() => {
              setMenu(null);
              archiveSession(s.id, true)
                .then(() => {
                  toast.success("Chat archived", { detail: "Restore it from the bot's workspace." });
                  onSessionsRevisionBump();
                })
                .catch(() => toast.error("Couldn't archive the chat"));
            }}
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

      <ProjectCreateModal
        open={showCreateProject}
        onClose={() => setShowCreateProject(false)}
        onCreated={() => { refreshProjects(); onSessionsRevisionBump(); }}
      />

      <TeamModal
        open={showTeamModal}
        onClose={() => setShowTeamModal(false)}
        onCreated={(sessionId, leadProjectId) => onSessionSelect(sessionId, leadProjectId)}
      />

      <DeleteBotModal
        bot={deleteBotTarget}
        onClose={() => setDeleteBotTarget(null)}
        onDeleted={() => { refreshProjects(); onSessionsRevisionBump(); }}
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
