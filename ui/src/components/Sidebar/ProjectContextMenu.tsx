import type { ProjectSummary } from "../../api/projects";

interface Props {
  project: ProjectSummary;
  anchorX: number;
  anchorY: number;
  onEdit: () => void;
  onDelete: () => void;
  onClick: (e: React.MouseEvent) => void;
}

export default function ProjectContextMenu({
  anchorX,
  anchorY,
  onEdit,
  onDelete,
  onClick,
}: Props) {
  const menuWidth = 180;
  const left = Math.min(anchorX, window.innerWidth - menuWidth - 8);
  const top = Math.min(anchorY, window.innerHeight - 120);

  return (
    <div
      className="sidebar-context-menu sidebar-context-menu--floating"
      style={{ top, left, width: menuWidth }}
      onClick={onClick}
    >
      <button className="sidebar-ctx-item" onClick={onEdit}>
        Edit project
      </button>
      <div className="sidebar-ctx-divider" />
      <button className="sidebar-ctx-item sidebar-ctx-item--danger" onClick={onDelete}>
        Delete project
      </button>
    </div>
  );
}
