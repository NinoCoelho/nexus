/**
 * AppsPane — the "Apps" main-area surface. Lists data-table apps (DuckDB
 * databases) and opens the selected one where chats render: dashboard →
 * table drill-down (VaultView) → ER diagram. Owns all app-selection state
 * (previously lifted in App.tsx); App only passes cross-view callbacks.
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
  /** App folder to open (e.g. from `#/apps/<folder>` deep link). */
  initialFolder?: string | null;
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

export default function AppsPane({ initialFolder, vaultViewCommon, onOpenInVault }: Props) {
  const [appDatabases, setAppDatabases] = useState<DatabaseSummary[]>([]);
  const [selectedDatabase, setSelectedDatabase] = useState<string | null>(initialFolder ?? null);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [diagramFolder, setDiagramFolder] = useState<string | null>(null);
  const [listRevision, setListRevision] = useState(0);

  useEffect(() => {
    listDatabases().then((r) => setAppDatabases(r.databases)).catch(() => {});
  }, [listRevision]);

  // Deep links (`#/apps/<folder>`) while already mounted: switch to that app.
  useEffect(() => {
    if (initialFolder) {
      setSelectedDatabase(initialFolder);
      setSelectedTable(null);
      setDiagramFolder(null);
    }
  }, [initialFolder]);

  useVaultEvents((ev) => {
    if (ev.type === "vault.indexed" || ev.type === "vault.removed") {
      listDatabases().then((r) => setAppDatabases(r.databases)).catch(() => {});
    }
  });

  const openApp = (folder: string) => {
    setSelectedDatabase(folder);
    setSelectedTable(null);
    setDiagramFolder(null);
  };

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
        {selectedDatabase !== null && (
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
            if (parent) setSelectedDatabase(parent);
          }}
        />
      </div>
    );
  }

  if (selectedDatabase !== null) {
    return (
      <Suspense fallback={<PaneFallback />}>
        <DataDashboardView
          folder={selectedDatabase}
          onOpenTable={(p) => setSelectedTable(p)}
          onOpenDiagram={(f) => { setDiagramFolder(f); setSelectedTable(null); }}
          onAfterDelete={() => {
            setSelectedDatabase(null);
            setSelectedTable(null);
            setDiagramFolder(null);
            setListRevision((n) => n + 1);
          }}
          onOpenInVault={onOpenInVault}
        />
      </Suspense>
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
            <button key={db.folder} className="apps-grid-card" onClick={() => openApp(db.folder)}>
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
