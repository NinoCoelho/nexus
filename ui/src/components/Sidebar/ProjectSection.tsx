import { useState } from "react";
import type { SessionSummary } from "../../api";
import type { ProjectSummary } from "../../api/projects";
import SessionItem from "./SessionItem";

interface Props {
  project: ProjectSummary;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  renamingId: string | null;
  renameValue: string;
  toVaultBusy: Set<string>;
  onSessionSelect: (id: string) => void;
  onContextMenu: (e: React.MouseEvent, id: string) => void;
  onMenuBtnClick: (e: React.MouseEvent, id: string) => void;
  onTitleDoubleClick: (e: React.MouseEvent, id: string, title: string) => void;
  onRenameChange: (v: string) => void;
  onRenameCommit: (id: string) => void;
  onRenameCancel: () => void;
  onProjectContextMenu?: (e: React.MouseEvent, projectId: string) => void;
  onNewChatInProject?: (projectId: string) => void;
}

export default function ProjectSection({
  project,
  sessions,
  activeSessionId,
  renamingId,
  renameValue,
  toVaultBusy,
  onSessionSelect,
  onContextMenu,
  onMenuBtnClick,
  onTitleDoubleClick,
  onRenameChange,
  onRenameCommit,
  onRenameCancel,
  onProjectContextMenu,
  onNewChatInProject,
}: Props) {
  const storageKey = `nx-project-collapsed-${project.id}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(storageKey) === "true";
    } catch {
      return false;
    }
  });

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(storageKey, String(next));
    } catch {}
  };

  return (
    <div className="sidebar-project-section">
      <button
        className="sidebar-project-header"
        onClick={toggle}
        onContextMenu={(e) => {
          e.preventDefault();
          onProjectContextMenu?.(e, project.id);
        }}
      >
        <span className={`sidebar-project-chevron${collapsed ? "" : " sidebar-project-chevron--open"}`}>
          <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
            <path d="M3 1l4 4-4 4z" />
          </svg>
        </span>
        {project.color && (
          <span
            className="sidebar-project-dot"
            style={{ background: project.color }}
          />
        )}
        {!project.color && (
          <span className="sidebar-project-dot sidebar-project-dot--default" />
        )}
        <span className="sidebar-project-name">{project.name}</span>
        <span className="sidebar-project-count">{sessions.length}</span>
        {onNewChatInProject && (
          <span
            className="sidebar-project-add-chat"
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onNewChatInProject(project.id);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.stopPropagation();
                onNewChatInProject(project.id);
              }
            }}
            title="New chat in project"
          >
            <svg width="10" height="10" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" />
              <line x1="4" y1="10" x2="16" y2="10" />
            </svg>
          </span>
        )}
      </button>
      {!collapsed && (
        <div className="sidebar-project-sessions">
          {sessions.map((s) => (
            <SessionItem
              key={s.id}
              session={s}
              isActive={s.id === activeSessionId}
              isRenaming={renamingId === s.id}
              renameValue={renameValue}
              toVaultBusy={toVaultBusy}
              onSelect={() => onSessionSelect(s.id)}
              onContextMenu={(e) => onContextMenu(e, s.id)}
              onMenuBtnClick={(e) => onMenuBtnClick(e, s.id)}
              onTitleDoubleClick={(e) => onTitleDoubleClick(e, s.id, s.title || "")}
              onRenameChange={onRenameChange}
              onRenameCommit={() => onRenameCommit(s.id)}
              onRenameCancel={onRenameCancel}
            />
          ))}
          {sessions.length === 0 && (
            <div className="sidebar-project-empty">No sessions yet</div>
          )}
        </div>
      )}
    </div>
  );
}
