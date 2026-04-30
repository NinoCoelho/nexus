/**
 * DataDashboardView — the home pane for a database.
 *
 * Renders database title, action chips (operations), an "ER diagram" button,
 * a table-card grid, and a "Delete database" affordance. Clicking a table
 * card opens it in DataTableView (via onOpenTable). Clicking a `chat`
 * operation chip fires the operation's prompt — for now via the parent's
 * `onRunOperation` callback, which Phase 3 wires to the floating bubble.
 *
 * Note: drill-down "Open in chat" buttons (e.g. on the related-rows panel
 * inside a table view) intentionally bypass this dashboard's bubble session
 * and land in the main ChatView. Bubble = scoped advisor for *this database*;
 * row-level dispatches branch into the full chat surface.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchDashboard,
  addOperation,
  deleteOperation,
  deleteDatabase,
  type Dashboard,
  type DashboardOperation,
} from "../../api/dashboard";
import {
  listDatabaseTables,
  getVaultDataTable,
  addVaultDataTableRow,
  type DatabaseTableSummary,
  type DataTable,
} from "../../api/datatable";
import { useToast } from "../../toast/ToastProvider";
import Modal from "../Modal";
import FormRenderer from "../FormRenderer";
import { deriveLabelInfo, suggestNextPk } from "../datatable/refOptions";
import OperationChips from "./OperationChips";
import AddOperationModal from "./AddOperationModal";
import DataChatBubble, { type DataChatBubbleHandle } from "../DataChatBubble";
import "./DataDashboardView.css";

interface Props {
  folder: string;
  onOpenTable: (path: string) => void;
  onOpenDiagram: (folder: string) => void;
  onAfterDelete: () => void;
}

export default function DataDashboardView({
  folder,
  onOpenTable,
  onOpenDiagram,
  onAfterDelete,
}: Props) {
  const toast = useToast();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [tables, setTables] = useState<DatabaseTableSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAddOp, setShowAddOp] = useState(false);
  const [formOp, setFormOp] = useState<{ op: DashboardOperation; table: DataTable } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typeToConfirm, setTypeToConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const bubbleRef = useRef<DataChatBubbleHandle>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [d, t] = await Promise.all([
        fetchDashboard(folder),
        listDatabaseTables(folder),
      ]);
      setDashboard(d);
      setTables(t.tables);
    } catch (e) {
      setError((e as Error).message ?? "failed to load dashboard");
    }
  }, [folder]);

  useEffect(() => { void reload(); }, [reload]);

  const handleRunOperation = useCallback(async (op: DashboardOperation) => {
    if (op.kind === "chat") {
      // Route chat-kind ops into the floating bubble (per-database session).
      // Drill-down "Open in chat" buttons on related-rows panels still go to
      // the main ChatView — the bubble is intentionally scoped to general
      // database operations, not branch conversations.
      bubbleRef.current?.runOperation(op.prompt || op.label);
      return;
    }
    // Form kind: load the target table's schema and pop a pre-filled form.
    if (!op.table) {
      toast.error("This operation has no target table.");
      return;
    }
    try {
      const tbl = await getVaultDataTable(op.table);
      setFormOp({ op, table: tbl });
    } catch (e) {
      toast.error("Couldn't load the target table.", { detail: (e as Error).message });
    }
  }, [toast]);

  const handleAddOperation = useCallback(async (op: DashboardOperation) => {
    try {
      const next = await addOperation(folder, op);
      setDashboard(next);
      setShowAddOp(false);
      toast.success(`Added "${op.label}"`);
    } catch (e) {
      toast.error("Couldn't add operation", { detail: (e as Error).message });
    }
  }, [folder, toast]);

  const handleRemoveOperation = useCallback(async (opId: string) => {
    try {
      const next = await deleteOperation(folder, opId);
      setDashboard(next);
    } catch (e) {
      toast.error("Couldn't remove operation", { detail: (e as Error).message });
    }
  }, [folder, toast]);

  const handleQuickAdd = useCallback(async (tablePath: string) => {
    try {
      const tbl = await getVaultDataTable(tablePath);
      setFormOp({
        op: {
          id: "_quickadd",
          label: `Add row in ${tbl.schema?.title ?? tablePath}`,
          kind: "form",
          prompt: "",
          table: tablePath,
        },
        table: tbl,
      });
    } catch (e) {
      toast.error("Couldn't load the table.", { detail: (e as Error).message });
    }
  }, [toast]);

  const handleFormSubmit = useCallback(async (values: Record<string, unknown>) => {
    if (!formOp) return;
    try {
      await addVaultDataTableRow(formOp.op.table!, values);
      toast.success(`Added row to ${formOp.op.table}`);
      setFormOp(null);
      void reload();
    } catch (e) {
      toast.error("Couldn't add row", { detail: (e as Error).message });
    }
  }, [formOp, reload, toast]);

  const folderBasename = folder.split("/").pop() || folder || "(root)";

  const handleDeleteDatabase = useCallback(async (typed: string) => {
    if (typed.trim() !== folderBasename) {
      setDeleteError(`Type "${folderBasename}" exactly to confirm.`);
      return;
    }
    try {
      const res = await deleteDatabase(folder, folderBasename);
      toast.success(`Deleted "${folderBasename}" (${res.deleted} files removed)`);
      setTypeToConfirm(false);
      setConfirmDelete(false);
      onAfterDelete();
    } catch (e) {
      setDeleteError((e as Error).message);
    }
  }, [folder, folderBasename, onAfterDelete, toast]);

  if (error) return <div className="dt-error" style={{ padding: 16 }}>{error}</div>;
  if (!dashboard || !tables) return <div className="dt-loading" style={{ padding: 16 }}>Loading…</div>;

  const totalRows = tables.reduce((sum, t) => sum + t.row_count, 0);

  return (
    <div className="data-dash">
      <header className="data-dash-header">
        <div className="data-dash-title-row">
          <h1 className="data-dash-title">{dashboard.title}</h1>
          <span className="data-dash-meta">
            {tables.length} table{tables.length === 1 ? "" : "s"} · {totalRows} row{totalRows === 1 ? "" : "s"}
          </span>
        </div>
        <div className="data-dash-actions">
          <button
            className="data-dash-action-btn"
            onClick={() => onOpenDiagram(folder)}
            title="Show ER diagram"
          >
            ER diagram
          </button>
          <button
            className="data-dash-action-btn data-dash-action-btn--danger"
            onClick={() => { setConfirmDelete(true); setDeleteError(null); }}
            title="Permanently delete this database"
          >
            Delete database
          </button>
        </div>
      </header>

      <section className="data-dash-section">
        <h2 className="data-dash-section-title">Quick actions</h2>
        <OperationChips
          operations={dashboard.operations}
          onRunOperation={(op) => void handleRunOperation(op)}
          onAddOperation={() => setShowAddOp(true)}
          onRemoveOperation={(id) => void handleRemoveOperation(id)}
        />
        {dashboard.operations.length === 0 && (
          <div className="data-dash-hint">
            No quick actions yet — chat with the <code>database-design</code> skill to suggest some, or click <strong>+ Operation</strong>.
          </div>
        )}
      </section>

      <section className="data-dash-section">
        <h2 className="data-dash-section-title">Tables</h2>
        {tables.length === 0 ? (
          <div className="data-dash-hint">
            No tables in this database yet. Open a chat and ask the agent to model your data.
          </div>
        ) : (
          <div className="data-dash-tables">
            {tables.map((t) => (
              <article key={t.path} className="data-dash-table-card">
                <header className="data-dash-table-card-head">
                  <h3 className="data-dash-table-card-title">{t.title}</h3>
                  <span className="data-dash-table-card-meta">
                    {t.row_count} row{t.row_count === 1 ? "" : "s"} · {t.field_count} col{t.field_count === 1 ? "" : "s"}
                  </span>
                </header>
                <div className="data-dash-table-card-actions">
                  <button
                    className="data-dash-action-btn"
                    onClick={() => onOpenTable(t.path)}
                  >
                    Open
                  </button>
                  <button
                    className="data-dash-action-btn"
                    onClick={() => void handleQuickAdd(t.path)}
                  >
                    Quick add
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {showAddOp && (
        <AddOperationModal
          folder={folder}
          tables={tables}
          onSubmit={handleAddOperation}
          onCancel={() => setShowAddOp(false)}
        />
      )}

      {formOp && (() => {
        // Auto-suggest the next primary-key value for the target table, so
        // Quick-add and form-kind operations don't make the user type C005,
        // P009, etc. by hand. Op-defined `prefill` values still win — the
        // op author may have intentionally set a specific id.
        const meta = formOp.table.schema.table ?? null;
        const { pkName } = deriveLabelInfo(formOp.table.schema.fields, meta);
        const suggested = suggestNextPk(formOp.table.rows, pkName);
        const initialValues: Record<string, unknown> = {
          ...(suggested ? { [pkName]: suggested } : {}),
          ...(formOp.op.prefill ?? {}),
        };
        return (
          <div className="dt-modal-overlay" onClick={() => setFormOp(null)}>
            <div className="dt-modal" onClick={(e) => e.stopPropagation()} style={{ minWidth: 420 }}>
              <div className="dt-modal-title">{formOp.op.label}</div>
              <FormRenderer
                hostPath={formOp.op.table!}
                fields={formOp.table.schema.fields.filter((f) => f.kind !== "formula")}
                initialValues={initialValues}
                onSubmit={(v) => void handleFormSubmit(v)}
                onCancel={() => setFormOp(null)}
                submitLabel="Add"
              />
            </div>
          </div>
        );
      })()}

      {confirmDelete && !typeToConfirm && (
        <Modal
          kind="confirm"
          title={`Delete "${folderBasename}"?`}
          message={`This will permanently remove ${tables.length} table${tables.length === 1 ? "" : "s"} and ${totalRows} row${totalRows === 1 ? "" : "s"}. This cannot be undone.`}
          confirmLabel="Continue"
          danger
          onSubmit={() => { setTypeToConfirm(true); setDeleteError(null); }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}

      {confirmDelete && typeToConfirm && (
        <Modal
          kind="prompt"
          title={`Type "${folderBasename}" to confirm`}
          message={
            deleteError
              ? deleteError
              : `Final check: type the database name exactly to delete it.`
          }
          placeholder={folderBasename}
          confirmLabel="Delete forever"
          onSubmit={(typed) => void handleDeleteDatabase(typed)}
          onCancel={() => { setTypeToConfirm(false); setConfirmDelete(false); }}
        />
      )}

      <DataChatBubble ref={bubbleRef} folder={folder} databaseTitle={dashboard.title} />
    </div>
  );
}
