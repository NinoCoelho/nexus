// Sidebar — floating position:fixed context menu for session actions.

import type { SessionSummary } from "../../api";
import type { ProjectSummary } from "../../api/projects";

interface Props {
  session: SessionSummary;
  anchorX: number;
  anchorY: number;
  toVaultBusy: Set<string>;
  projects: ProjectSummary[];
  onRename: () => void;
  onExport: () => void;
  onToVaultRaw: () => void;
  onToVaultSummary: () => void;
  onDelete: () => void;
  onArchive?: () => void;
  onClick: (e: React.MouseEvent) => void;
  onMoveToProject?: (projectId: string) => void;
  onRemoveFromProject?: () => void;
}

export default function SessionContextMenu({
  session,
  anchorX,
  anchorY,
  toVaultBusy,
  projects,
  onRename,
  onExport,
  onToVaultRaw,
  onToVaultSummary,
  onDelete,
  onArchive,
  onClick,
  onMoveToProject,
  onRemoveFromProject,
}: Props) {
  const menuWidth = 200;
  // Keep the menu on-screen: if the anchor is too close to the right edge,
  // flip to the left of the cursor/button.
  const left = Math.min(anchorX, window.innerWidth - menuWidth - 8);
  const top = Math.min(anchorY, window.innerHeight - 240);

  return (
    <div
      className="sidebar-context-menu sidebar-context-menu--floating"
      style={{ top, left, width: menuWidth }}
      onClick={onClick}
    >
      <button className="sidebar-ctx-item" onClick={onRename}>
        Rename
      </button>
      <button className="sidebar-ctx-item" onClick={onExport}>
        Download .md
      </button>
      <div className="sidebar-ctx-divider" />
      <button
        className="sidebar-ctx-item"
        disabled={toVaultBusy.has(session.id)}
        onClick={onToVaultRaw}
        title="Save the full transcript to the vault"
      >
        Send to vault (raw)
      </button>
      <button
        className="sidebar-ctx-item"
        disabled={toVaultBusy.has(session.id)}
        onClick={onToVaultSummary}
        title="Have Nexus summarize this session and save the note"
      >
        Send to vault (summary)
      </button>
      <div className="sidebar-ctx-divider" />
      {session.project_id && onRemoveFromProject && (
        <button className="sidebar-ctx-item" onClick={onRemoveFromProject}>
          Remove from bot
        </button>
      )}
      {onMoveToProject && projects.length > 0 && (
        <div className="sidebar-ctx-submenu">
          <div className="sidebar-ctx-item sidebar-ctx-item--disabled">Move to bot</div>
          {projects
            .filter((p) => p.id !== session.project_id)
            .map((p) => (
              <button
                key={p.id}
                className="sidebar-ctx-item sidebar-ctx-item--indent"
                onClick={() => onMoveToProject(p.id)}
              >
                {p.color && (
                  <span className="sidebar-ctx-dot" style={{ background: p.color }} />
                )}
                {p.name}
              </button>
            ))}
        </div>
      )}
      {(session.project_id && onRemoveFromProject) || (onMoveToProject && projects.length > 0) ? (
        <div className="sidebar-ctx-divider" />
      ) : null}
      {onArchive && (
        <button className="sidebar-ctx-item" onClick={onArchive} title="Hide this chat from active lists — restorable from the bot's workspace">
          Archive
        </button>
      )}
      <button className="sidebar-ctx-item sidebar-ctx-item--danger" onClick={onDelete}>
        Delete
      </button>
    </div>
  );
}
