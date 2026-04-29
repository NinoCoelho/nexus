/**
 * KanbanListPanel — flat, vault-wide list of every kanban board.
 *
 * Boards are detected by `kanban-plugin:` frontmatter and may live anywhere
 * under the vault root. Click an entry to load the board into the main pane
 * via the existing VaultEditorPanel / KanbanBoard renderer.
 */

import { useCallback, useEffect, useState } from "react";
import { listKanbanBoards, type KanbanBoardSummary } from "../../api";
import "./KanbanListPanel.css";

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
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load boards");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <div className="kanban-list-panel">
      <div className="kanban-list-header">
        <span className="kanban-list-title">Boards{boards ? ` · ${boards.length}` : ""}</span>
        <button
          className="kanban-list-refresh"
          onClick={() => void refresh()}
          disabled={loading}
          title="Reload boards"
          aria-label="Reload boards"
        >
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 4 3 9 8 9" />
            <polyline points="17 16 17 11 12 11" />
            <path d="M5 9a6 6 0 0 1 10-2.5L17 9" />
            <path d="M15 11a6 6 0 0 1-10 2.5L3 11" />
          </svg>
        </button>
      </div>

      {error && <div className="kanban-list-error">{error}</div>}

      {!error && boards && boards.length === 0 && (
        <div className="kanban-list-empty">
          No kanban boards yet — create a markdown file with{" "}
          <code>kanban-plugin: basic</code> in its frontmatter.
        </div>
      )}

      {boards && boards.length > 0 && (
        <ul className="kanban-list">
          {boards.map((b) => (
            <li key={b.path}>
              <button
                className={`kanban-list-item${selectedPath === b.path ? " kanban-list-item--active" : ""}`}
                onClick={() => onOpen(b.path)}
                title={b.path}
              >
                <span className="kanban-list-item-title">{b.title}</span>
                <span className="kanban-list-item-path">{b.path}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
