/**
 * AppsPane — the "Apps" main-area surface. Renders the selected data-table
 * app (DuckDB database) where chats render: dashboard → table drill-down
 * (VaultView) → ER diagram. App selection is lifted to App (the sidebar
 * AppsListPanel and `#/apps/<folder>` deep links share it); table/diagram
 * drill-down state is local. Styling: shared view language (views.css).
 */

import { lazy, Suspense, useEffect, useState } from "react";
import { ArrowLeft, Database } from "lucide-react";
import { listDatabases, type DatabaseSummary } from "../../api/datatable";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import VaultView from "../VaultView";
import "./views.css";

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
      <div className="view-pane-col">
        {selectedFolder !== null && (
          <div className="view-toolbar">
            <button className="view-back-btn" onClick={() => setSelectedTable(null)}>
              <ArrowLeft size={13} /> Back to dashboard
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
      <div className="view-pane-col">
        <div className="view-toolbar">
          <button className="view-back-btn" onClick={() => onSelectFolder(null)}>
            <ArrowLeft size={13} /> All apps
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
    <div className="view-pane-col">
      <div className="view-head">
        <h2 className="view-title">Apps</h2>
        <p className="view-sub">Data-table apps built from your vault</p>
      </div>
      <div className="view-body">
        {appDatabases.length === 0 ? (
          <div className="view-empty">
            <Database size={26} className="view-empty-icon" />
            <p>No apps yet</p>
            <p className="view-empty-hint">Import a CSV in the Vault to create one</p>
          </div>
        ) : (
          <div className="view-grid">
            {appDatabases.map((db) => (
              <button key={db.folder} className="view-card" onClick={() => onSelectFolder(db.folder)}>
                <span className="view-card-icon">{db.icon || db.title.charAt(0).toUpperCase()}</span>
                <span className="view-card-title">{db.title}</span>
                <span className="view-card-meta">
                  {db.table_count} {db.table_count === 1 ? "table" : "tables"}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
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
