/**
 * KanbanListPanel — sidebar panel listing every kanban board (detected by
 * `kanban-plugin:` frontmatter anywhere in the vault). Clicking a board
 * opens it in the main pane via the VaultEditorPanel / KanbanBoard
 * renderer. Selection is lifted to App (deep links share it).
 */

import { useCallback, useEffect, useState } from "react";
import { listKanbanBoards, type KanbanBoardSummary } from "../../api";

interface Props {
  selectedPath: string | null;
  onOpen: (path: string) => void;
}

export default function KanbanListPanel({ selectedPath, onOpen }: Props) {
  const [boards, setBoards] = useState<KanbanBoardSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listKanbanBoards();
      setBoards(res.boards);
    } catch {
      setError("Couldn't load boards.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <div className="sidebar-section sidebar-kanban-section">
      <div className="sidebar-sessions-header">
        <div className="sidebar-section-label">Boards{boards ? ` · ${boards.length}` : ""}</div>
        <button
          className="sidebar-collapse-all-btn"
          onClick={() => void refresh()}
          disabled={loading}
          title="Reload boards"
          aria-label="Reload boards"
        >
          <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 4 3 9 8 9" />
            <polyline points="17 16 17 11 12 11" />
            <path d="M5 9a6 6 0 0 1 10-2.5L17 9" />
            <path d="M15 11a6 6 0 0 1-10 2.5L3 11" />
          </svg>
          Refresh
        </button>
      </div>
      {error && <div className="sidebar-error">{error}</div>}
      <div className="sidebar-project-list">
        {(boards ?? []).map((b) => (
          <button
            key={b.path}
            className={`sidebar-project-row${selectedPath === b.path ? " sidebar-project-row--active" : ""}`}
            onClick={() => onOpen(b.path)}
            title={b.path}
          >
            <span className="sidebar-project-row-name">{b.title}</span>
          </button>
        ))}
        {boards !== null && boards.length === 0 && !error && (
          <div className="sidebar-project-empty">
            No boards yet — a markdown file with <code>kanban-plugin: basic</code> becomes one.
          </div>
        )}
      </div>
    </div>
  );
}
