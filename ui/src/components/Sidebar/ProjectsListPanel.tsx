/**
 * ProjectsListPanel — sidebar panel listing projects as a single-expanded
 * accordion: each project header expands to reveal its chats (one project
 * expanded at a time — expanding another collapses the current). Clicking a
 * chat opens it in the Projects view (selection/state lifted to App).
 * Right-click a project header for edit/delete.
 */

import { useEffect, useMemo, useState } from "react";
import { listProjects, type ProjectSummary } from "../../api/projects";
import type { SessionSummary } from "../../api";
import ProjectContextMenu from "./ProjectContextMenu";
import ProjectEditModal from "./ProjectEditModal";

interface Props {
  selectedId: string | null;
  /** The single expanded project id (accordion state lives in App so
   * cross-surface jumps can expand the right project). */
  expandedId: string | null;
  onSelect: (id: string | null) => void;
  onToggleExpand: (id: string) => void;
  onNewProject: () => void;
  onNewChat: (projectId: string) => void;
  onSessionSelect: (id: string, projectId?: string | null) => void;
  activeSessionId?: string | null;
  /** Project sessions (grouped here by project_id). */
  sessions: SessionSummary[];
  refreshKey: number;
  /** Bumped after mutations so the parent's session data refreshes too. */
  onChanged?: () => void;
}

export default function ProjectsListPanel({
  selectedId,
  expandedId,
  onSelect,
  onToggleExpand,
  onNewProject,
  onNewChat,
  onSessionSelect,
  activeSessionId = null,
  sessions,
  refreshKey,
  onChanged,
}: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState(false);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [editId, setEditId] = useState<string | null>(null);

  const reload = () => {
    listProjects().then(setProjects).catch(() => setError(true));
  };

  useEffect(() => {
    reload();
  }, [refreshKey]);

  useEffect(() => {
    if (!menu) return;
    const handler = () => setMenu(null);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [menu]);

  const sessionsByProject = useMemo(() => {
    const map = new Map<string, SessionSummary[]>();
    for (const s of sessions) {
      if (!s.project_id) continue;
      const list = map.get(s.project_id) || [];
      list.push(s);
      map.set(s.project_id, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => {
        const av = typeof a.updated_at === "number" ? a.updated_at : Date.parse(a.updated_at) / 1000;
        const bv = typeof b.updated_at === "number" ? b.updated_at : Date.parse(b.updated_at) / 1000;
        return bv - av;
      });
    }
    return map;
  }, [sessions]);

  return (
    <div className="sidebar-section sidebar-projects-section">
      <div className="sidebar-sessions-header">
        <div className="sidebar-section-label">Projects</div>
        <button
          className="sidebar-collapse-all-btn"
          onClick={onNewProject}
          title="New project"
        >
          <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="10" y1="4" x2="10" y2="16" />
            <line x1="4" y1="10" x2="16" y2="10" />
          </svg>
          New
        </button>
      </div>
      {error && <div className="sidebar-error">Couldn&apos;t load projects.</div>}
      <div className="sidebar-project-list">
        {projects.map((p) => {
          const chats = sessionsByProject.get(p.id) ?? [];
          const expanded = expandedId === p.id;
          return (
            <div key={p.id} className="sidebar-project-item">
              <div className="sidebar-project-item-row">
                <button
                  className={`sidebar-project-header${selectedId === p.id ? " sidebar-project-header--active" : ""}`}
                  onClick={() => (selectedId === p.id ? onToggleExpand(p.id) : onSelect(p.id))}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setMenu({ id: p.id, x: e.clientX, y: e.clientY });
                  }}
                  title={`${p.name} — right-click for options`}
                  aria-expanded={expanded}
                >
                  <svg
                    className={`sidebar-project-chevron${expanded ? " sidebar-project-chevron--open" : ""}`}
                    width="10"
                    height="10"
                    viewBox="0 0 16 16"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M6 4l4 4-4 4" />
                  </svg>
                  <span className="sidebar-project-dot" style={{ background: p.color || "var(--accent)" }} />
                  <span className="sidebar-project-name">{p.name}</span>
                  <span className="sidebar-project-count">{chats.length}</span>
                </button>
                <button
                  className="sidebar-project-add-chat"
                  title="New chat in project"
                  onClick={(e) => {
                    e.stopPropagation();
                    onNewChat(p.id);
                  }}
                >
                  <svg width="11" height="11" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <line x1="10" y1="4" x2="10" y2="16" />
                    <line x1="4" y1="10" x2="16" y2="10" />
                  </svg>
                </button>
              </div>
              {expanded && (
                <div className="sidebar-project-sessions">
                  {chats.length === 0 && <div className="sidebar-project-empty">No chats yet</div>}
                  {chats.map((s) => (
                    <button
                      key={s.id}
                      className={`sidebar-project-chat-row${activeSessionId === s.id ? " sidebar-project-chat-row--active" : ""}`}
                      onClick={() => onSessionSelect(s.id, p.id)}
                      title={s.title || "Untitled"}
                    >
                      <span className="sidebar-project-chat-name">{s.title || "Untitled"}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {projects.length === 0 && !error && (
          <div className="sidebar-project-empty">No projects yet</div>
        )}
      </div>

      {menu && (() => {
        const p = projects.find((x) => x.id === menu.id);
        if (!p) return null;
        return (
          <ProjectContextMenu
            project={p}
            anchorX={menu.x}
            anchorY={menu.y}
            onEdit={() => { setEditId(p.id); setMenu(null); }}
            onDelete={async () => {
              const { deleteProject } = await import("../../api/projects");
              deleteProject(p.id).then(() => {
                reload();
                onChanged?.();
              }).catch(() => {});
              setMenu(null);
            }}
            onClick={(e) => e.stopPropagation()}
          />
        );
      })()}

      <ProjectEditModal
        open={editId !== null}
        projectId={editId}
        onClose={() => setEditId(null)}
        onSaved={() => { reload(); onChanged?.(); }}
      />
    </div>
  );
}
