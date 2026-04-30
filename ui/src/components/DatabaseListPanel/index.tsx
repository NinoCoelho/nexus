/**
 * DatabaseListPanel — left-sidebar list of databases (folders containing
 * ≥1 data-table file). Clicking a database header opens its dashboard;
 * the inline table list expands as a secondary navigation aid. Clicking
 * a table inside the list opens that table directly in the main pane.
 */

import { useCallback, useEffect, useState } from "react";
import {
  listDatabases,
  listDatabaseTables,
  type DatabaseSummary,
  type DatabaseTableSummary,
} from "../../api/datatable";
import "./DatabaseListPanel.css";

interface Props {
  selectedPath: string | null;
  selectedDatabase?: string | null;
  onOpen: (path: string) => void;
  /** Click on a database header → open its dashboard. Primary action. */
  onSelectDatabase?: (folder: string) => void;
  onOpenDiagram?: (folder: string) => void;
}

export default function DatabaseListPanel({
  selectedPath,
  selectedDatabase,
  onOpen,
  onSelectDatabase,
  onOpenDiagram,
}: Props) {
  const [databases, setDatabases] = useState<DatabaseSummary[] | null>(null);
  const [tablesByFolder, setTablesByFolder] = useState<Record<string, DatabaseTableSummary[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refreshDatabases = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listDatabases();
      setDatabases(res.databases);
      // Auto-expand the first database for one-click visibility.
      if (res.databases.length > 0) {
        setExpanded((prev) => prev.size === 0 ? new Set([res.databases[0].folder]) : prev);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load databases");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refreshDatabases(); }, [refreshDatabases]);

  // Fetch tables for any expanded folder we don't yet have.
  useEffect(() => {
    const missing = Array.from(expanded).filter((f) => !(f in tablesByFolder));
    if (missing.length === 0) return;
    let cancelled = false;
    (async () => {
      const updates: Record<string, DatabaseTableSummary[]> = {};
      for (const folder of missing) {
        try {
          const res = await listDatabaseTables(folder);
          updates[folder] = res.tables;
        } catch {
          updates[folder] = [];
        }
      }
      if (!cancelled) {
        setTablesByFolder((prev) => ({ ...prev, ...updates }));
      }
    })();
    return () => { cancelled = true; };
  }, [expanded, tablesByFolder]);

  const handleHeaderClick = (folder: string) => {
    // Primary action: open the dashboard.
    onSelectDatabase?.(folder);
    // Secondary: ensure the inline table list is visible. Toggle only when the
    // user clicks the header of the *already-active* database (lets them
    // collapse).
    setExpanded((prev) => {
      const next = new Set(prev);
      if (selectedDatabase === folder && next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  };

  return (
    <div className="db-list-panel">
      <div className="db-list-header">
        <span className="db-list-title">
          Data{databases ? ` · ${databases.length}` : ""}
        </span>
        <button
          className="db-list-refresh"
          onClick={() => {
            setTablesByFolder({});
            void refreshDatabases();
          }}
          disabled={loading}
          title="Reload databases"
          aria-label="Reload databases"
        >
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 4 3 9 8 9" />
            <polyline points="17 16 17 11 12 11" />
            <path d="M5 9a6 6 0 0 1 10-2.5L17 9" />
            <path d="M15 11a6 6 0 0 1-10 2.5L3 11" />
          </svg>
        </button>
      </div>

      {error && <div className="db-list-error">{error}</div>}

      {!error && databases && databases.length === 0 && (
        <div className="db-list-empty">
          No databases yet — any folder containing a markdown file with{" "}
          <code>data-table-plugin: basic</code> shows up here.
        </div>
      )}

      {databases && databases.length > 0 && (
        <ul className="db-list">
          {databases.map((db) => {
            const isOpen = expanded.has(db.folder);
            const tables = tablesByFolder[db.folder];
            return (
              <li key={db.folder} className="db-list-group">
                <div className="db-list-group-head">
                  <button
                    className={`db-list-group-toggle${
                      selectedDatabase === db.folder ? " db-list-group-toggle--active" : ""
                    }`}
                    onClick={() => handleHeaderClick(db.folder)}
                    title={db.folder || "(root)"}
                    aria-expanded={isOpen}
                  >
                    <span className={`db-list-caret${isOpen ? " db-list-caret--open" : ""}`}>
                      ▸
                    </span>
                    <span className="db-list-group-title">{db.title}</span>
                    <span className="db-list-group-count">{db.table_count}</span>
                  </button>
                  {onOpenDiagram && (
                    <button
                      className="db-list-diagram-btn"
                      onClick={() => onOpenDiagram(db.folder)}
                      title="Show ER diagram"
                      aria-label="Show ER diagram"
                    >
                      ER
                    </button>
                  )}
                </div>
                {isOpen && (
                  <ul className="db-list-tables">
                    {tables === undefined && (
                      <li className="db-list-loading">loading…</li>
                    )}
                    {tables && tables.length === 0 && (
                      <li className="db-list-loading">no tables</li>
                    )}
                    {tables && tables.map((t) => (
                      <li key={t.path}>
                        <button
                          className={`db-list-table-item${
                            selectedPath === t.path ? " db-list-table-item--active" : ""
                          }`}
                          onClick={() => onOpen(t.path)}
                          title={t.path}
                        >
                          <span className="db-list-table-title">{t.title}</span>
                          <span className="db-list-table-meta">
                            {t.row_count} row{t.row_count === 1 ? "" : "s"} · {t.field_count} col{t.field_count === 1 ? "" : "s"}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
