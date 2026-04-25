// Sidebar — a single session row in the session list.

import type { SessionSummary } from "../../api";
import { fmtRelative } from "./utils";

interface Props {
  session: SessionSummary;
  isActive: boolean;
  isRenaming: boolean;
  renameValue: string;
  toVaultBusy: Set<string>;
  onSelect: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onMenuBtnClick: (e: React.MouseEvent) => void;
  onTitleDoubleClick: (e: React.MouseEvent) => void;
  onRenameChange: (v: string) => void;
  onRenameCommit: () => void;
  onRenameCancel: () => void;
}

export default function SessionItem({
  session,
  isActive,
  isRenaming,
  renameValue,
  toVaultBusy,
  onSelect,
  onContextMenu,
  onMenuBtnClick,
  onTitleDoubleClick,
  onRenameChange,
  onRenameCommit,
  onRenameCancel,
}: Props) {
  return (
    <div
      className={`sidebar-session${isActive ? " sidebar-session--active" : ""}`}
      onClick={onSelect}
      onContextMenu={onContextMenu}
    >
      {isRenaming ? (
        <input
          className="sidebar-session-rename"
          value={renameValue}
          autoFocus
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onRenameChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onRenameCommit();
            if (e.key === "Escape") onRenameCancel();
          }}
          onBlur={onRenameCancel}
        />
      ) : (
        <>
          <span
            className="sidebar-session-title"
            onDoubleClick={onTitleDoubleClick}
            title="Double-click to rename"
          >
            {session.title || "Untitled"}
            {toVaultBusy.has(session.id) && " ⋯"}
          </span>
          <span className="sidebar-session-time">{fmtRelative(session.updated_at)}</span>
          <button
            className="sidebar-session-menu-btn"
            aria-label="Session actions"
            onClick={onMenuBtnClick}
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
  );
}
