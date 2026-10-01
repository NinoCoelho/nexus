/**
 * AppsPane — the "Apps" main-area surface. Renders the selected data-table
 * app (DuckDB database) where chats render: dashboard → table drill-down
 * (VaultView) → ER diagram. App selection is lifted to App (the sidebar
 * AppsListPanel and `#/apps/<folder>` deep links share it); table/diagram
 * drill-down state is local.
 */

import { lazy, Suspense, useEffect, useState } from "react";
import { ArrowLeft, Database } from "lucide-react";
import { listDatabases, type DatabaseSummary } from "../../api/datatable";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import VaultView from "../VaultView";
import "./AppsPane.css";

const DatabaseSchemaView = lazy(() => import("../DatabaseSchemaView"));
const DataDashboardView = lazy(() => import("../DataDashboardView"));

interface Props {
  /** Selected app folder — controlled by App (sidebar list + deep links). */
  selectedFolder: string | null;
  onSelectFolder: (folder: string | null) => void;
  /** Cross-view callbacks shared with the Vault view instance. */
  vaultViewCommon: {
    onDispatchToChat: (sessionId: string, seedMessage: string) => void;
    onOpenInChat: (sessionId: string, seedMessage: string, title: string, model?: string) => void;
    onNavigateToSession: (sessionId: string) => void;
    onViewEntityGraph: (path: string) => void;
    onOpenCalendar: (path: string) => void;
    onOpenInVault: (path: string) => void;
    onOpenWorkflow: (path: string) => void;
  };
  onOpenInVault: (path: string) => void;
}

export default function AppsPane({ selectedFolder, onSelectFolder, vaultViewCommon, onOpenInVault }: Props) {
  const [appDatabases, setAppDatabases] = useState<DatabaseSummary[]>([]);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [diagramFolder, setDiagramFolder] = useState<string | null>(null);
  const [listRevision, setListRevision] = useState(0);

  useEffect(() => {
    listDatabases().then((r) => setAppDatabases(r.databases)).catch(() => {});
  }, [listRevision]);

  useVaultEvents((ev) => {
    if (ev.type === "vault.indexed" || ev.type === "vault.removed") {
      listDatabases().then((r) => setAppDatabases(r.databases)).catch(() => {});
    }
  });

  // Switching apps (from the sidebar or a deep link) resets the drill-down.
  useEffect(() => {
    setSelectedTable(null);
    setDiagramFolder(null);
  }, [selectedFolder]);

  if (diagramFolder !== null) {
    return (
      <Suspense fallback={<PaneFallback />}>
        <DatabaseSchemaView folder={diagramFolder} onClose={() => setDiagramFolder(null)} />
      </Suspense>
    );
  }

  if (selectedTable) {
    return (
      <div className="apps-pane apps-pane--table">
        {selectedFolder !== null && (
          <div className="apps-pane-toolbar">
            <button
              className="dt-action-btn"
              onClick={() => setSelectedTable(null)}
              title="Back to dashboard"
            >
              <ArrowLeft size={14} /> Back to dashboard
            </button>
          </div>
        )}
        <VaultView
          selectedPath={selectedTable}
          {...vaultViewCommon}
          onOpenTable={(p: string) => {
            setSelectedTable(p);
            setDiagramFolder(null);
            const parent = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
            if (parent && parent !== selectedFolder) onSelectFolder(parent);
          }}
        />
      </div>
    );
  }

  if (selectedFolder !== null) {
    return (
      <div className="apps-pane apps-pane--dashboard">
        <div className="apps-pane-toolbar">
          <button
            className="dt-action-btn"
            onClick={() => onSelectFolder(null)}
            title="Back to all apps"
          >
            <ArrowLeft size={14} /> All apps
          </button>
        </div>
        <Suspense fallback={<PaneFallback />}>
          <DataDashboardView
            folder={selectedFolder}
            onOpenTable={(p) => setSelectedTable(p)}
            onOpenDiagram={(f) => { setDiagramFolder(f); setSelectedTable(null); }}
            onAfterDelete={() => {
              onSelectFolder(null);
              setSelectedTable(null);
              setDiagramFolder(null);
              setListRevision((n) => n + 1);
            }}
            onOpenInVault={onOpenInVault}
          />
        </Suspense>
      </div>
    );
  }

  return (
    <div className="apps-pane apps-pane--grid">
      <div className="apps-pane-header">
        <h2>Apps</h2>
        <p className="apps-pane-sub">
          Data-table apps built from your vault. Import a CSV in the Vault to create one.
        </p>
      </div>
      {appDatabases.length === 0 ? (
        <div className="apps-pane-empty">
          <Database size={28} />
          <p>No apps yet.</p>
          <p className="apps-pane-empty-hint">
            Drag a CSV into the Vault tree and choose "promote to data-table app".
          </p>
        </div>
      ) : (
        <div className="apps-grid">
          {appDatabases.map((db) => (
            <button key={db.folder} className="apps-grid-card" onClick={() => onSelectFolder(db.folder)}>
              <span className={`apps-grid-icon${db.icon ? " apps-grid-icon--emoji" : ""}`}>
                {db.icon || db.title.charAt(0).toUpperCase()}
              </span>
              <span className="apps-grid-title">{db.title}</span>
              <span className="apps-grid-desc">
                {db.table_count} {db.table_count === 1 ? "table" : "tables"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function PaneFallback() {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span className="gs-spinner" style={{ width: 22, height: 22, borderWidth: 3 }} />
    </div>
  );
}
