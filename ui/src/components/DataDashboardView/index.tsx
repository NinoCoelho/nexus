/**
 * DataDashboardView — the home pane for a database.
 *
 * Renders database title, action chips (operations), an "ER diagram" button,
 * a table-card grid, and a "Delete database" affordance. Clicking a table
 * card opens it in DataTableView (via onOpenTable). Clicking a `chat`
 * operation chip kicks an ephemeral *hidden* agent session (server marks
 * the session hidden, so it never lands in the sidebar). The chip itself
 * shows live status — spinner while running, brief check on success, a
 * persistent warning on failure. Clicking the status icon opens the run's
 * transcript in `CardActivityModal` for inspection.
 *
 * Note: drill-down "Open in chat" buttons (e.g. on the related-rows panel
 * inside a table view) intentionally bypass this flow and land in the
 * main ChatView. The bubble is the database's free-form advisor and is
 * still available for the user — actions just no longer feed into it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchDashboard,
  addOperation,
  deleteOperation,
  deleteDatabase,
  runOperation,
  planOperation,
  executeOperation,
  fetchRunHistory,
  addWidget,
  deleteWidget,
  designWidget,
  type Dashboard,
  type DashboardOperation,
  type DashboardWidget,
} from "../../api/dashboard";
import { deleteSession } from "../../api/sessions";
import {
  listDatabaseTables,
  getVaultDataTable,
  addVaultDataTableRow,
  type DatabaseTableSummary,
  type DataTable,
} from "../../api/datatable";
import { subscribeSessionEvents } from "../../api/chat";
import { useToast } from "../../toast/ToastProvider";
import Modal from "../Modal";
import FormRenderer from "../FormRenderer";
import { deriveLabelInfo, suggestNextPk } from "../datatable/refOptions";
import OperationChips, { type OpRunState } from "./OperationChips";
import AddOperationModal from "./AddOperationModal";
import WidgetGrid from "./WidgetGrid";
import DashboardWizard from "./DashboardWizard";
import PlanReviewModal from "./PlanReviewModal";
import DataChatBubble, { type DataChatBubbleHandle } from "../DataChatBubble";
import CardActivityModal from "../CardActivityModal";
import WidgetSQLEditor from "./WidgetSQLEditor";
import "./DataDashboardView.css";

interface Props {
  folder: string;
  onOpenTable: (path: string) => void;
  onOpenDiagram: (folder: string) => void;
  onAfterDelete: () => void;
  onOpenInVault?: (path: string) => void;
}

export default function DataDashboardView({
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
  const [showAddOp, setShowAddOp] = useState(false);
  const [showWidgetWizard, setShowWidgetWizard] = useState(false);
  const [editingWidget, setEditingWidget] = useState<DashboardWidget | null>(null);
  const [showOpWizard, setShowOpWizard] = useState(false);
  const [sqlEditWidget, setSqlEditWidget] = useState<DashboardWidget | null>(null);
  const [aiFixContext, setAiFixContext] = useState<{ widget: DashboardWidget; error: string } | null>(null);
  // Active plan-review for an op marked ``preview: true`` — populated when
  // the user clicks the chip and we kick a plan-only run instead of the
  // real one. Cleared on approve / cancel.
  const [planReview, setPlanReview] = useState<
    { op: DashboardOperation; sessionId: string } | null
  >(null);
  const [pendingWidgetRemoval, setPendingWidgetRemoval] = useState<DashboardWidget | null>(null);
  const [formOp, setFormOp] = useState<{ op: DashboardOperation; table: DataTable } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typeToConfirm, setTypeToConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const bubbleRef = useRef<DataChatBubbleHandle>(null);
  // Per-op last-run state. In-memory only — resets when the user navigates
  // away from this database. Persistence into `_data.md` would just clutter
  // the file with ephemeral run history.
  const [runState, setRunState] = useState<Record<string, OpRunState>>({});
  // The op whose run the user just clicked open (CardActivityModal).
  const [openRun, setOpenRun] = useState<{ op: DashboardOperation; state: OpRunState } | null>(null);
  // The op the user just clicked × on — held until they confirm or cancel.
  // Without this, an accidental click silently destroys the action and any
  // pre-fill defaults (or prompt) the user took the time to author.
  const [pendingRemoval, setPendingRemoval] = useState<DashboardOperation | null>(null);
  // Auto-clear the green "done" tick after a few seconds so the chip goes
  // back to its quiet resting state on success — failures stick.
  const fadeTimers = useRef<Record<string, number>>({});
  // SSE subscriptions for in-flight runs, keyed by op id. Closed on unmount
  // and when a new run replaces an older one for the same op.
  const runSubs = useRef<Record<string, { close: () => void }>>({});
  useEffect(() => () => {
    Object.values(fadeTimers.current).forEach((id) => window.clearTimeout(id));
    Object.values(runSubs.current).forEach((s) => s.close());
  }, []);

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

  // Hydrate per-op runState from persisted hidden sessions on mount /
  // folder switch. Failures seed visible warning chips (so the user notices
  // an action that broke earlier even after a reload); orphaned successes
  // (run finished after the user navigated away) get GC'd straight away —
  // the success tick is purely a live-feedback affordance.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const history = await fetchRunHistory(folder);
        if (cancelled) return;
        const next: Record<string, OpRunState> = {};
        for (const r of history.runs) {
          if (r.status === "failed") {
            next[r.op_id] = {
              sessionId: r.session_id,
              status: "failed",
              error: r.error ?? undefined,
            };
          } else {
            // Stale success — discard the session so it doesn't pile up.
            void deleteSession(r.session_id).catch(() => {
              /* benign — likely already gone */
            });
          }
        }
        setRunState((prev) => {
          // Don't clobber any in-flight runs the user kicked while we were
          // fetching — those win over hydrated state.
          const merged: Record<string, OpRunState> = { ...next };
          for (const [opId, state] of Object.entries(prev)) {
            if (state.status === "running") merged[opId] = state;
          }
          return merged;
        });
      } catch {
        // Hydration is best-effort: a network blip shouldn't block the UI.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [folder]);

  // Wires the running op session into runState + auto-pop CardActivityModal
  // when it terminates. Used for both the direct path and the post-approval
  // execute path so they behave identically once kicked.
  const trackChatOpSession = useCallback((op: DashboardOperation, sessionId: string) => {
    const prevTimer = fadeTimers.current[op.id];
    if (prevTimer) {
      window.clearTimeout(prevTimer);
      delete fadeTimers.current[op.id];
    }
    const prevSub = runSubs.current[op.id];
    if (prevSub) {
      prevSub.close();
      delete runSubs.current[op.id];
    }
    setRunState((s) => ({ ...s, [op.id]: { sessionId, status: "running" } }));
    const sub = subscribeSessionEvents(sessionId, (event) => {
      if (event.kind !== "op_done") return;
      const ok = event.data.status === "done";
      const finalStatus: OpRunState["status"] = ok ? "done" : "failed";
      setRunState((s) =>
        s[op.id]?.sessionId === sessionId
          ? {
              ...s,
              [op.id]: {
                sessionId,
                status: finalStatus,
                error: ok ? undefined : event.data.error ?? undefined,
              },
            }
          : s,
      );
      setOpenRun({
        op,
        state: { sessionId, status: finalStatus, error: ok ? undefined : event.data.error ?? undefined },
      });
      const current = runSubs.current[op.id];
      if (current) {
        current.close();
        delete runSubs.current[op.id];
      }
    });
    runSubs.current[op.id] = sub;
  }, []);

  const handleRunOperation = useCallback(async (op: DashboardOperation) => {
    if (op.kind === "chat") {
      // Preview-flagged ops route through plan-then-execute. The plan run
      // itself is hidden from the chip status — only the post-approval
      // execute drives runState — so a cancelled plan leaves no trace.
      if (op.preview) {
        try {
          const { session_id } = await planOperation(folder, op.id);
          setPlanReview({ op, sessionId: session_id });
        } catch (e) {
          toast.error("Couldn't build a plan.", { detail: (e as Error).message });
        }
        return;
      }
      // Kick an ephemeral *hidden* session — never appears in the sidebar.
      // The chip surfaces status via `runState`; the user can click the
      // status icon to open the run in CardActivityModal.
      try {
        const result = await runOperation(folder, op.id);
        trackChatOpSession(op, result.session_id);
      } catch (e) {
        toast.error("Couldn't start action.", { detail: (e as Error).message });
        setRunState((s) => {
          const { [op.id]: _gone, ...rest } = s;
          void _gone;
          return rest;
        });
      }
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
  }, [folder, toast, trackChatOpSession]);

  // Plan-review → execute. Called from PlanReviewModal once the user clicks
  // Approve. Closes the modal and kicks the real run, which then drives the
  // chip via the same trackChatOpSession path direct runs use.
  const handleApprovePlan = useCallback(async (op: DashboardOperation, approvedPlan: string) => {
    setPlanReview(null);
    try {
      const result = await executeOperation(folder, op.id, approvedPlan);
      trackChatOpSession(op, result.session_id);
    } catch (e) {
      toast.error("Couldn't start the approved run.", { detail: (e as Error).message });
    }
  }, [folder, toast, trackChatOpSession]);

  const handleOpenRun = useCallback((op: DashboardOperation) => {
    const state = runState[op.id];
    if (!state) return;
    setOpenRun({ op, state });
  }, [runState]);

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

  const handleEditWidget = useCallback(async (widget: DashboardWidget) => {
    try {
      const next = await addWidget(folder, widget);
      setDashboard(next);
      setShowWidgetWizard(false);
      setEditingWidget(null);
      toast.success(`Saved "${widget.title}"`);
    } catch (e) {
      toast.error("Couldn't save widget", { detail: (e as Error).message });
    }
  }, [folder, toast]);

  const handleDesignWidget = useCallback((widget: DashboardWidget) => {
    const goal = widget.prompt || `Redesign widget "${widget.title}" with a better query and visualization`;
    void (async () => {
      try {
        const { session_id: _sid } = await designWidget(folder, widget.id, goal);
        void _sid;
        toast.info(`Designing "${widget.title}"\u2026`, { detail: "The agent is inspecting your schema and planning a query." });
      } catch (e) {
        toast.error("Couldn't start design", { detail: (e as Error).message });
      }
    })();
  }, [folder, toast]);

  const handleSqlEditSave = useCallback(async (widget: DashboardWidget) => {
    try {
      const next = await addWidget(folder, widget);
      setDashboard(next);
      setSqlEditWidget(null);
      toast.success(`Saved "${widget.title}"`);
    } catch (e) {
      toast.error("Couldn't save widget", { detail: (e as Error).message });
    }
  }, [folder, toast]);

  const handleResizeWidget = useCallback(async (widget: DashboardWidget, size: "sm" | "md" | "lg") => {
    if (widget.size === size) return;
    // Optimistic local update so the click feels instant — server upsert
    // races below and only the dashboard list is reconciled.
    setDashboard((d) =>
      d
        ? {
            ...d,
            widgets: (d.widgets ?? []).map((w) => (w.id === widget.id ? { ...w, size } : w)),
          }
        : d,
    );
    try {
      const next = await addWidget(folder, { ...widget, size });
      setDashboard(next);
    } catch (e) {
      toast.error("Couldn't resize widget", { detail: (e as Error).message });
      // Roll back the optimistic update by triggering a fresh load.
      void reload();
    }
  }, [folder, reload, toast]);

  const handleRemoveWidget = useCallback(async (widgetId: string) => {
    try {
      const next = await deleteWidget(folder, widgetId);
      setDashboard(next);
    } catch (e) {
      toast.error("Couldn't remove widget", { detail: (e as Error).message });
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
          runState={runState}
          onRunOperation={(op) => void handleRunOperation(op)}
          onOpenRun={handleOpenRun}
          onAddOperation={() => setShowAddOp(true)}
          onAddOperationWizard={() => setShowOpWizard(true)}
          onRemoveOperation={(id) => {
            const op = dashboard.operations.find((o) => o.id === id);
            if (op) setPendingRemoval(op);
          }}
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

      <section className="data-dash-section">
        <h2 className="data-dash-section-title">Widgets</h2>
        <WidgetGrid
          folder={folder}
          widgets={dashboard.widgets ?? []}
          onAddWizard={() => setShowWidgetWizard(true)}
          onEdit={(w) => setEditingWidget(w)}
          onRemove={(id) => {
            const w = (dashboard.widgets ?? []).find((x) => x.id === id);
            if (w) setPendingWidgetRemoval(w);
          }}
          onResize={handleResizeWidget}
          onDesign={handleDesignWidget}
          onSqlEdit={(w) => setSqlEditWidget(w)}
          onAIFix={(w, error) => setAiFixContext({ widget: w, error })}
        />
      </section>

      {pendingWidgetRemoval && (
        <Modal
          kind="confirm"
          title={`Remove "${pendingWidgetRemoval.title}"?`}
          message="This deletes the widget and its saved result. The query is gone \u2014 re-create it from + Widget."
          confirmLabel="Remove"
          danger
          onSubmit={() => {
            const w = pendingWidgetRemoval;
            setPendingWidgetRemoval(null);
            void handleRemoveWidget(w.id);
          }}
          onCancel={() => setPendingWidgetRemoval(null)}
        />
      )}

      {showAddOp && (
        <AddOperationModal
          folder={folder}
          tables={tables}
          onSubmit={handleAddOperation}
          onCancel={() => setShowAddOp(false)}
        />
      )}

      {(showWidgetWizard || editingWidget) && !aiFixContext && (
        <DashboardWizard
          folder={folder}
          kind="widget"
          editing={editingWidget}
          onApproveWidget={async (w) => {
            await handleEditWidget(w);
          }}
          onCancel={() => {
            setShowWidgetWizard(false);
            setEditingWidget(null);
          }}
        />
      )}

      {aiFixContext && (
        <DashboardWizard
          folder={folder}
          kind="widget"
          initialGoal={
            `Fix the widget "${aiFixContext.widget.title}". The query failed with this error:\n` +
            `${aiFixContext.error}\n\n` +
            `Current query:\n${aiFixContext.widget.query}\n\n` +
            `Please output a corrected nexus-widget-proposal with a working SQL query. ` +
            `Keep the same viz_type ("${aiFixContext.widget.viz_type}") and widget id ("${aiFixContext.widget.id}").`
          }
          onApproveWidget={async (w) => {
            await handleEditWidget(w);
            setAiFixContext(null);
          }}
          onCancel={() => setAiFixContext(null)}
        />
      )}

      {sqlEditWidget && (
        <WidgetSQLEditor
          folder={folder}
          widget={sqlEditWidget}
          onClose={() => setSqlEditWidget(null)}
          onSaved={handleSqlEditSave}
        />
      )}

      {showOpWizard && (
        <DashboardWizard
          folder={folder}
          kind="operation"
          onApproveOperation={async (op) => {
            await handleAddOperation(op);
            setShowOpWizard(false);
          }}
          onCancel={() => setShowOpWizard(false)}
        />
      )}

      {planReview && (
        <PlanReviewModal
          operation={planReview.op}
          sessionId={planReview.sessionId}
          onApprove={(approved) => handleApprovePlan(planReview.op, approved)}
          onCancel={() => setPlanReview(null)}
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

      {pendingRemoval && (
        <Modal
          kind="confirm"
          title={`Remove "${pendingRemoval.label}"?`}
          message="This deletes the action from the dashboard. You can re-create it from + Operation."
          confirmLabel="Remove"
          danger
          onSubmit={() => {
            const op = pendingRemoval;
            setPendingRemoval(null);
            void handleRemoveOperation(op.id);
          }}
          onCancel={() => setPendingRemoval(null)}
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

      {openRun && (
        <CardActivityModal
          sessionId={openRun.state.sessionId}
          cardTitle={openRun.op.label}
          status={openRun.state.status}
          onClose={() => {
            // Closing the popup is the user's "I saw it" — drop the chip
            // warning and GC the underlying hidden session. Skip while the
            // run is still in flight (user opened the live spinner): closing
            // mid-run shouldn't cancel the run or its state.
            const closed = openRun;
            setOpenRun(null);
            if (closed.state.status !== "running") {
              setRunState((s) => {
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
