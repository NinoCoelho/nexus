/**
 * ProjectsListPanel — sidebar panel listing projects (like SessionsPanel
 * for chats). Selection is lifted to App so the main-area ProjectsPane
 * workspace and this list stay in sync.
 */

import { useEffect, useState } from "react";
import { listProjects, type ProjectSummary } from "../../api/projects";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewProject: () => void;
  refreshKey: number;
}

export default function ProjectsListPanel({ selectedId, onSelect, onNewProject, refreshKey }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch(() => setError(true));
  }, [refreshKey]);

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
          >
            <span className="sidebar-project-dot" style={{ background: p.color || "var(--accent, #888)" }} />
            <span className="sidebar-project-row-name">{p.name}</span>
            <span className="sidebar-project-count">{p.session_count}</span>
          </button>
        ))}
        {projects.length === 0 && !error && (
          <div className="sidebar-project-empty">No projects yet</div>
        )}
      </div>
    </div>
  );
}
