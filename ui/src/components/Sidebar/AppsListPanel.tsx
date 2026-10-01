/**
 * AppsListPanel — sidebar panel listing data-table apps (like VaultTreePanel
 * for vault files). Selection is lifted to App so the main-area AppsPane and
 * this list stay in sync; clicking the selected app deselects it (back to
 * the app grid).
 */

import { useEffect, useState } from "react";
import { listDatabases, type DatabaseSummary } from "../../api/datatable";
import { useVaultEvents } from "../../hooks/useVaultEvents";

interface Props {
  selectedFolder: string | null;
  onSelect: (folder: string | null) => void;
}

export default function AppsListPanel({ selectedFolder, onSelect }: Props) {
  const [apps, setApps] = useState<DatabaseSummary[]>([]);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    listDatabases()
      .then((r) => setApps(r.databases))
      .catch(() => setError(true));
  }, [revision]);

  useVaultEvents((ev) => {
    if (ev.type === "vault.indexed" || ev.type === "vault.removed") {
      setRevision((n) => n + 1);
    }
  });

  return (
    <div className="sidebar-section sidebar-apps-section">
      <div className="sidebar-section-label">Apps</div>
      {error && <div className="sidebar-error">Couldn&apos;t load apps.</div>}
      <div className="sidebar-app-list">
        {apps.map((db) => {
          const active = selectedFolder === db.folder;
          return (
            <button
              key={db.folder}
              className={`sidebar-app-row${active ? " sidebar-app-row--active" : ""}`}
              onClick={() => onSelect(active ? null : db.folder)}
              title={active ? "Back to all apps" : db.title}
            >
              <span className={`sidebar-app-icon${db.icon ? " sidebar-app-icon--emoji" : ""}`}>
                {db.icon || db.title.charAt(0).toUpperCase()}
              </span>
              <span className="sidebar-app-row-name">{db.title}</span>
              <span className="sidebar-project-count">{db.table_count}</span>
            </button>
          );
        })}
        {apps.length === 0 && !error && (
          <div className="sidebar-project-empty">
            No apps yet — import a CSV in the Vault to create one.
          </div>
        )}
      </div>
    </div>
  );
}
