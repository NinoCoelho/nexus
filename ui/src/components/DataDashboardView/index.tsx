import { useCallback, useEffect, useRef, useState } from "react";
import { Calendar, Check, ClipboardList } from "lucide-react";
import {
  fetchDashboard,
  deleteDatabase,
  type Dashboard,
  type DashboardOperation,
} from "../../api/dashboard";
import { deleteSession } from "../../api/sessions";
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
import { deriveLabelInfo, suggestNextPk, resolveRefPath, fetchTableCached, summarizeRow } from "../datatable/refOptions";
import RefCombobox from "../Combobox";
import OperationChips from "./OperationChips";
import AddOperationModal from "./AddOperationModal";
import WidgetGrid from "./WidgetGrid";
import DashboardWizard from "./DashboardWizard";
import PlanReviewModal from "./PlanReviewModal";
import DataChatBubble, { type DataChatBubbleHandle } from "../DataChatBubble";
import CardActivityModal from "../CardActivityModal";
import WidgetSQLEditor from "./WidgetSQLEditor";
import { useDashboardOperations } from "./useDashboardOperations";
import { useDashboardWidgets } from "./useDashboardWidgets";
import "./DataDashboardView.css";

function useResolvedLabels(fields: import("../../types/form").FieldSchema[], hostPath: string) {
  const [lookup, setLookup] = useState<Map<string, string>>(new Map());
  const refFields = fields.filter((f) => f.kind === "ref" && f.target_table);
  useEffect(() => {
    if (refFields.length === 0) return;
    let cancelled = false;
    const next = new Map<string, string>();
    void (async () => {
      const targets = new Map<string, { pkName: string; labelField: import("../../types/form").FieldSchema | null; rows: Record<string, unknown>[] }>();
      for (const f of refFields) {
        const absPath = resolveRefPath(hostPath, f.target_table!);
        if (targets.has(absPath)) continue;
        try {
          const tbl = await fetchTableCached(absPath);
          const info = deriveLabelInfo(tbl.schema.fields, tbl.schema.table);
          targets.set(absPath, { pkName: info.pkName, labelField: info.labelField, rows: tbl.rows });
        } catch { /* skip */ }
      }
      if (cancelled) return;
      for (const f of refFields) {
        const absPath = resolveRefPath(hostPath, f.target_table!);
        const t = targets.get(absPath);
        if (!t) continue;
        for (const r of t.rows) {
          const id = String(r[t.pkName] ?? r._id ?? "");
          if (id) next.set(`${f.name}::${id}`, summarizeRow(r, t.pkName, t.labelField));
        }
      }
      if (!cancelled) setLookup(next);
    })();
    return () => { cancelled = true; };
  }, [hostPath, refFields.length]);
  return lookup;
}

function QuickAddChildForm({
  childPath,
  fields,
  fkField,
  parentPk,
  disabled,
  existingRows,
  onAdded,
}: {
  childPath: string;
  fields: import("../../types/form").FieldSchema[];
  fkField: string;
  parentPk: string;
  disabled: boolean;
  existingRows: Record<string, unknown>[];
  onAdded: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [addedRows, setAddedRows] = useState<Record<string, unknown>[]>([]);
  const visibleFields = fields.filter((f) => f.name !== fkField);
  const displayFields = visibleFields.filter((f) => f.kind !== "formula" && f.kind !== "rollup").slice(0, 5);
  const { pkName } = deriveLabelInfo(fields, null);
  const refLookup = useResolvedLabels(fields, childPath);

  const resetForm = useCallback(() => {
    const allRows = [...existingRows, ...addedRows];
    const suggested = suggestNextPk(allRows, pkName);
    setValues(suggested ? { [pkName]: suggested } : {});
  }, [existingRows, addedRows, pkName]);

  useEffect(() => { resetForm(); }, [addedRows]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = useCallback(async () => {
    setBusy(true);
    try {
      const row = { ...values, [fkField]: parentPk };
      await addVaultDataTableRow(childPath, row);
      toast.success("Detail row added");
      setAddedRows((prev) => [...prev, row]);
      onAdded();
    } catch (e) {
      toast.error("Add failed", { detail: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }, [childPath, fkField, parentPk, values, onAdded, toast]);

  if (disabled) return <div className="qa-detail-hint">Save the parent row first to add details.</div>;

  const renderCell = (val: unknown, f: import("../../types/form").FieldSchema) => {
    if (val == null || val === "") return "—";
    if (f.kind === "ref") return refLookup.get(`${f.name}::${val}`) ?? String(val);
    return String(val);
  };

  return (
    <div className="qa-child-section">
      {addedRows.length > 0 && (
        <div className="qa-mini-table-wrap">
          <table className="qa-mini-table">
            <thead>
              <tr>
                {displayFields.map((f) => (
                  <th key={f.name}>{f.label ?? f.name}</th>
                ))}
                <th className="qa-mini-th-act"></th>
              </tr>
            </thead>
            <tbody>
              {addedRows.map((r, i) => (
                <tr key={i}>
                  {displayFields.map((f) => (
                    <td key={f.name}>{renderCell(r[f.name], f)}</td>
                  ))}
                  <td className="qa-mini-td-act">
                    <button className="qa-added-remove" onClick={() => setAddedRows((prev) => prev.filter((_, j) => j !== i))} title="Remove">×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="qa-inline-form">
        {visibleFields.map((f) => (
          <div key={f.name} className="qa-inline-field">
            <label className="qa-inline-label">{f.label ?? f.name}{f.required && " *"}</label>
            {f.kind === "ref" ? (
              <RefCombobox
                field={f}
                hostPath={childPath}
                value={values[f.name] ?? ""}
                onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
                className="qa-inline-input"
              />
            ) : (
              <input
                className="qa-inline-input"
                type={f.kind === "number" ? "number" : f.kind === "date" ? "date" : "text"}
                placeholder={f.placeholder ?? ""}
                value={String(values[f.name] ?? "")}
                onChange={(e) => {
                  const v = f.kind === "number" && e.target.value !== "" ? parseFloat(e.target.value) : e.target.value;
                  setValues((prev) => ({ ...prev, [f.name]: v }));
                }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void handleSubmit(); } }}
              />
            )}
          </div>
        ))}
        <button className="qa-inline-add" onClick={() => void handleSubmit()} disabled={busy}>
          {busy ? "Adding..." : "+ Add line"}
        </button>
      </div>
    </div>
  );
}

interface Props {
  folder: string;
  onOpenTable: (path: string) => void;
  onOpenDiagram: (folder: string) => void;
  onAfterDelete: () => void;
  onOpenInVault?: (path: string) => void;
}

export default function AppDashboardView({
  folder,
  onOpenTable,
  onOpenDiagram,
  onAfterDelete,
  onOpenInVault: _onOpenInVault,
}: Props) {
  const toast = useToast();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [tables, setTables] = useState<DatabaseTableSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formOp, setFormOp] = useState<{ op: DashboardOperation; table: DataTable } | null>(null);
  const [formCreatedRow, setFormCreatedRow] = useState<Record<string, unknown> | null>(null);
  const [formChildTables, setFormChildTables] = useState<{ path: string; table: DataTable; fkField: string }[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typeToConfirm, setTypeToConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const bubbleRef = useRef<DataChatBubbleHandle>(null);

  const ops = useDashboardOperations({
    folder,
    setDashboard,
    toast,
    onFormOp: useCallback((op: DashboardOperation, table: DataTable) => {
      setFormOp({ op, table });
    }, []),
  });

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

  const widgets = useDashboardWidgets({
    folder,
    setDashboard,
    reload,
    toast,
  });

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
      setFormCreatedRow(null);
      setFormChildTables([]);
      if (!tables) return;
      const children: { path: string; table: DataTable; fkField: string }[] = [];
      for (const sibling of tables) {
        if (sibling.path === tablePath) continue;
        try {
          const sibTbl = await getVaultDataTable(sibling.path);
          for (const f of sibTbl.schema.fields) {
            if (f.kind === "ref" && f.cardinality !== "many") {
              const resolved = resolveRefPath(sibling.path, f.target_table ?? "");
              if (resolved === tablePath) {
                children.push({ path: sibling.path, table: sibTbl, fkField: f.name });
                break;
              }
            }
          }
        } catch { /* skip unreadable tables */ }
      }
      setFormChildTables(children);
    } catch (e) {
      toast.error("Couldn't load the table.", { detail: (e as Error).message });
    }
  }, [tables, folder, toast]);

  const handleFormSubmit = useCallback(async (values: Record<string, unknown>) => {
    if (!formOp) return;
    try {
      const created = await addVaultDataTableRow(formOp.op.table!, values);
      toast.success(`Added row to ${formOp.op.table}`);
      if (formChildTables.length > 0) {
        setFormCreatedRow(created);
      } else {
        setFormOp(null);
        void reload();
      }
    } catch (e) {
      toast.error("Couldn't add row", { detail: (e as Error).message });
    }
  }, [formOp, formChildTables.length, reload, toast]);

  const handleCloseForm = useCallback(() => {
    setFormOp(null);
    setFormCreatedRow(null);
    void reload();
  }, [reload]);

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

      <section className="data-dash-section">
        <h2 className="data-dash-section-title">Quick actions</h2>
        <OperationChips
          operations={dashboard.operations}
          runState={ops.runState}
          onRunOperation={(op) => void ops.handleRunOperation(op)}
          onOpenRun={ops.handleOpenRun}
          onAddOperation={() => ops.setShowAddOp(true)}
          onAddOperationWizard={() => ops.setShowOpWizard(true)}
          onRemoveOperation={(id) => {
            const op = dashboard.operations.find((o) => o.id === id);
            if (op) ops.setPendingRemoval(op);
          }}
        />
        {dashboard.operations.length === 0 && (
          <div className="data-dash-hint">
            No quick actions yet — chat with the <code>database-design</code> skill to suggest some, or click <strong>+ Operation</strong>.
          </div>
        )}
      </section>

      <section className="data-dash-section">
        <h2 className="data-dash-section-title">Widgets</h2>
        <WidgetGrid
          folder={folder}
          widgets={dashboard.widgets ?? []}
          onAddWizard={() => widgets.setShowWidgetWizard(true)}
          onEdit={(w) => widgets.setEditingWidget(w)}
          onRemove={(id) => {
            const w = (dashboard.widgets ?? []).find((x) => x.id === id);
            if (w) widgets.setPendingWidgetRemoval(w);
          }}
          onResize={widgets.handleResizeWidget}
          onDesign={widgets.handleDesignWidget}
          onSqlEdit={(w) => widgets.setSqlEditWidget(w)}
          onAIFix={(w, err) => widgets.setAiFixContext({ widget: w, error: err })}
        />
      </section>

      {dashboard.links && (dashboard.links.boards.length > 0 || dashboard.links.calendars.length > 0) && (
        <section className="data-dash-section">
          <h2 className="data-dash-section-title">Processes</h2>
          <div className="data-dash-links">
            {dashboard.links.boards.map((path) => (
              <button
                key={path}
                className="data-dash-link-card"
                onClick={() => _onOpenInVault?.(path.replace(/^\.\//, `${folder}/`))}
              >
                <span className="data-dash-link-icon"><ClipboardList size={16} /></span>
                <span className="data-dash-link-name">
                  {path.replace(/^\.\//, "").replace(".md", "").replace(/[-_]/g, " ")}
                </span>
              </button>
            ))}
            {dashboard.links.calendars.map((path) => (
              <button
                key={path}
                className="data-dash-link-card"
                onClick={() => _onOpenInVault?.(path.replace(/^\.\//, `${folder}/`))}
              >
                <span className="data-dash-link-icon"><Calendar size={16} /></span>
                <span className="data-dash-link-name">
                  {path.replace(/^\.\//, "").replace(".md", "").replace(/[-_]/g, " ")}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {widgets.pendingWidgetRemoval && (
        <Modal
          kind="confirm"
          title={`Remove "${widgets.pendingWidgetRemoval.title}"?`}
          message="This deletes the widget and its saved result. The query is gone \u2014 re-create it from + Widget."
          confirmLabel="Remove"
          danger
          onSubmit={() => {
            const w = widgets.pendingWidgetRemoval!;
            widgets.setPendingWidgetRemoval(null);
            void widgets.handleRemoveWidget(w.id);
          }}
          onCancel={() => widgets.setPendingWidgetRemoval(null)}
        />
      )}

      {ops.showAddOp && (
        <AddOperationModal
          folder={folder}
          tables={tables}
          onSubmit={ops.handleAddOperation}
          onCancel={() => ops.setShowAddOp(false)}
        />
      )}

      {(widgets.showWidgetWizard || widgets.editingWidget) && !widgets.aiFixContext && (
        <DashboardWizard
          folder={folder}
          kind="widget"
          editing={widgets.editingWidget}
          onApproveWidget={async (w) => {
            await widgets.handleEditWidget(w);
          }}
          onCancel={() => {
            widgets.setShowWidgetWizard(false);
            widgets.setEditingWidget(null);
          }}
        />
      )}

      {widgets.aiFixContext && (
        <DashboardWizard
          folder={folder}
          kind="widget"
          initialGoal={
            `Fix the widget "${widgets.aiFixContext.widget.title}". The query failed with this error:\n` +
            `${widgets.aiFixContext.error}\n\n` +
            `Current query:\n${widgets.aiFixContext.widget.query}\n\n` +
            `Please output a corrected nexus-widget-proposal with a working SQL query. ` +
            `Keep the same viz_type ("${widgets.aiFixContext.widget.viz_type}") and widget id ("${widgets.aiFixContext.widget.id}").`
          }
          onApproveWidget={async (w) => {
            await widgets.handleEditWidget(w);
            widgets.setAiFixContext(null);
          }}
          onCancel={() => widgets.setAiFixContext(null)}
        />
      )}

      {widgets.sqlEditWidget && (
        <WidgetSQLEditor
          folder={folder}
          widget={widgets.sqlEditWidget}
          onClose={() => widgets.setSqlEditWidget(null)}
          onSaved={widgets.handleSqlEditSave}
        />
      )}

      {ops.showOpWizard && (
        <DashboardWizard
          folder={folder}
          kind="operation"
          onApproveOperation={async (op) => {
            await ops.handleAddOperation(op);
            ops.setShowOpWizard(false);
          }}
          onCancel={() => ops.setShowOpWizard(false)}
        />
      )}

      {ops.planReview && (
        <PlanReviewModal
          operation={ops.planReview.op}
          sessionId={ops.planReview.sessionId}
          onApprove={(approved) => ops.handleApprovePlan(ops.planReview!.op, approved)}
          onCancel={() => ops.setPlanReview(null)}
        />
      )}

      {formOp && (() => {
        const meta = formOp.table.schema.table ?? null;
        const { pkName } = deriveLabelInfo(formOp.table.schema.fields, meta);
        const suggested = suggestNextPk(formOp.table.rows, pkName);
        const initialValues: Record<string, unknown> = {
          ...(suggested ? { [pkName]: suggested } : {}),
          ...(formOp.op.prefill ?? {}),
        };
        const parentPk = formCreatedRow ? String(formCreatedRow[pkName] ?? "") : "";
        return (
          <div className="dt-modal-overlay" onClick={() => handleCloseForm()}>
            <div className="dt-modal" onClick={(e) => e.stopPropagation()} style={{ minWidth: 480, maxWidth: 600 }}>
              <div className="dt-modal-title">{formOp.op.label}</div>
              {!formCreatedRow ? (
                <FormRenderer
                  hostPath={formOp.op.table!}
                  fields={formOp.table.schema.fields.filter((f) => f.kind !== "formula" && f.kind !== "rollup")}
                  initialValues={initialValues}
                  onSubmit={(v) => void handleFormSubmit(v)}
                  onCancel={() => handleCloseForm()}
                  submitLabel="Save & continue"
                />
              ) : (
                <div className="qa-master-readonly">
                  <div className="qa-master-header">
                    <span className="qa-master-check"><Check size={14} /></span>
                    <span className="qa-master-pk">{formOp.table.schema?.title ?? "Row"} <strong>{parentPk}</strong></span>
                  </div>
                  <div className="qa-master-fields">
                    {formOp.table.schema.fields.filter((f) => f.kind !== "formula" && f.kind !== "rollup" && f.name !== "_id").map((f) => (
                      <div key={f.name} className="qa-master-field">
                        <span className="qa-master-field-label">{f.label ?? f.name}</span>
                        <span className="qa-master-field-value">{String(formCreatedRow[f.name] ?? "—")}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {formChildTables.length > 0 && (
                <div className="qa-detail-section">
                  <div className="qa-detail-title">Details</div>
                  {formChildTables.map((child) => {
                    const childFields = child.table.schema.fields.filter((f) => (f.kind ?? "text") !== "formula" && (f.kind ?? "text") !== "rollup");
                    return (
                      <div key={child.path} className="qa-child-group">
                        <div className="qa-child-group-title">{child.table.schema?.title ?? child.path}</div>
                        <QuickAddChildForm
                          childPath={child.path}
                          fields={childFields}
                          fkField={child.fkField}
                          parentPk={parentPk}
                          disabled={!formCreatedRow}
                          existingRows={child.table.rows}
                          onAdded={() => void reload()}
                        />
                      </div>
                    );
                  })}
                </div>
              )}
              {formCreatedRow && (
                <div className="qa-done-row">
                  <button className="dt-action-btn dt-action-btn--primary" onClick={() => handleCloseForm()}>
                    Done
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {ops.pendingRemoval && (
        <Modal
          kind="confirm"
          title={`Remove "${ops.pendingRemoval.label}"?`}
          message="This deletes the action from the dashboard. You can re-create it from + Operation."
          confirmLabel="Remove"
          danger
          onSubmit={() => {
            const op = ops.pendingRemoval!;
            ops.setPendingRemoval(null);
            void ops.handleRemoveOperation(op.id);
          }}
          onCancel={() => ops.setPendingRemoval(null)}
        />
      )}

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

      <DataChatBubble
        ref={bubbleRef}
        folder={folder}
        databaseTitle={dashboard.title}
        onTurnComplete={() => void reload()}
      />

      {ops.openRun && (
        <CardActivityModal
          sessionId={ops.openRun.state.sessionId}
          cardTitle={ops.openRun.op.label}
          status={ops.openRun.state.status}
          onClose={() => {
            const closed = ops.openRun!;
            ops.setOpenRun(null);
            if (closed.state.status !== "running") {
              ops.setRunState((s) => {
                if (s[closed.op.id]?.sessionId !== closed.state.sessionId) return s;
                const { [closed.op.id]: _gone, ...rest } = s;
                void _gone;
                return rest;
              });
              void deleteSession(closed.state.sessionId).catch(() => { /* benign */ });
            }
          }}
        />
      )}
    </div>
  );
}
