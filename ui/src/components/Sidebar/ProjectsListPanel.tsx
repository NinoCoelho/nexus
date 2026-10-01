/**
 * ProjectsListPanel — sidebar panel listing projects (like SessionsPanel
 * for chats). Selection is lifted to App so the main-area ProjectsPane
 * workspace and this list stay in sync. Right-click a project for
 * edit/delete.
 */

import { useEffect, useState } from "react";
import { listProjects, type ProjectSummary } from "../../api/projects";
import ProjectContextMenu from "./ProjectContextMenu";
import ProjectEditModal from "./ProjectEditModal";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewProject: () => void;
  refreshKey: number;
  /** Bumped after mutations so the parent's session data refreshes too. */
  onChanged?: () => void;
}

export default function ProjectsListPanel({ selectedId, onSelect, onNewProject, refreshKey, onChanged }: Props) {
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
        {projects.map((p) => (
          <button
            key={p.id}
            className={`sidebar-project-row${selectedId === p.id ? " sidebar-project-row--active" : ""}`}
            onClick={() => onSelect(p.id)}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu({ id: p.id, x: e.clientX, y: e.clientY });
            }}
            title={`${p.name} — right-click for options`}
          >
            <span className="sidebar-project-dot" style={{ background: p.color || "var(--accent)" }} />
            <span className="sidebar-project-row-name">{p.name}</span>
            <span className="sidebar-project-count">{p.session_count}</span>
          </button>
        ))}
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
