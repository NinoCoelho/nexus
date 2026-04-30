/**
 * AddOperationModal — small form to define a new dashboard operation.
 *
 * Operations are quick actions exposed as chips on the database dashboard.
 * "chat" kind kicks an ephemeral hidden agent session with a pre-set prompt;
 * "form" kind opens an inline pre-filled add-row modal for a target table.
 *
 * Form-kind authoring renders the actual target-table form so the user can
 * fill the values they want pre-filled — instead of writing JSON.
 */

import { useEffect, useMemo, useState } from "react";
import type { DashboardOperation, OperationKind } from "../../api/dashboard";
import { getVaultDataTable, type DataTable, type DatabaseTableSummary } from "../../api/datatable";
import type { FieldSchema } from "../../types/form";
import FormRenderer from "../FormRenderer";

interface Props {
  folder: string;
  tables: DatabaseTableSummary[];
  onSubmit: (op: DashboardOperation) => void | Promise<void>;
  onCancel: () => void;
}

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

/** Drop empty values so we only persist fields the user actually filled in. */
function compact(values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(values)) {
    if (v === undefined || v === null) continue;
    if (typeof v === "string" && v === "") continue;
    if (Array.isArray(v) && v.length === 0) continue;
    out[k] = v;
  }
  return out;
}

export default function AddOperationModal({ folder: _folder, tables, onSubmit, onCancel }: Props) {
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState<OperationKind>("chat");
  const [table, setTable] = useState(tables[0]?.path ?? "");
  const [prompt, setPrompt] = useState("");
  // Form-kind only: live default values typed into the rendered FormRenderer.
  const [prefill, setPrefill] = useState<Record<string, unknown>>({});
  const [tableSchema, setTableSchema] = useState<DataTable | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const id = useMemo(() => slug(label || "op"), [label]);
  const canSubmit =
    label.trim().length > 0 &&
    (kind === "chat" || (kind === "form" && !!table));

  // Lazy-fetch the table schema only when the user actually picks a form-kind
  // target — chat-kind authoring shouldn't pay this cost. Resets prefill so
  // values from one table don't leak into another.
  useEffect(() => {
    setPrefill({});
    if (kind !== "form" || !table) {
      setTableSchema(null);
      setSchemaError(null);
      return;
    }
    let cancelled = false;
    setSchemaLoading(true);
    setSchemaError(null);
    getVaultDataTable(table)
      .then((t) => {
        if (!cancelled) setTableSchema(t);
      })
      .catch((e) => {
        if (!cancelled) setSchemaError((e as Error).message ?? "failed to load table");
      })
      .finally(() => {
        if (!cancelled) setSchemaLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [kind, table]);

  // Stripped-required copy of the schema fields so the user can leave any
  // field blank — empty fields just mean "ask the user when the action runs".
  // Formula fields are computed at row-add time, so excluding them keeps the
  // prefill surface clean.
  const prefillFields: FieldSchema[] = useMemo(() => {
    if (!tableSchema) return [];
    return tableSchema.schema.fields
      .filter((f) => f.kind !== "formula")
      .map((f) => ({ ...f, required: false }));
  }, [tableSchema]);

  async function handleSubmit() {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const compactPrefill = kind === "form" ? compact(prefill) : undefined;
      const op: DashboardOperation = {
        id: `op_${id}`,
        label: label.trim(),
        kind,
        // Form kind ignores prompt server-side; keep it empty.
        prompt: kind === "chat" ? prompt.trim() : "",
        ...(kind === "form" ? { table } : {}),
        ...(compactPrefill && Object.keys(compactPrefill).length > 0
          ? { prefill: compactPrefill }
          : {}),
      };
      await onSubmit(op);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div className="dt-modal" onClick={(e) => e.stopPropagation()} style={{ minWidth: 460 }}>
        <div className="dt-modal-title">New action</div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Label</label>
          <input
            className="form-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Add customer"
            autoFocus
          />
        </div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Kind</label>
          <select
            className="form-input"
            value={kind}
            onChange={(e) => setKind(e.target.value as OperationKind)}
          >
            <option value="chat">Chat — run a prompt with the agent</option>
            <option value="form">Form — open a pre-filled add-row form</option>
          </select>
        </div>

        {kind === "form" && (
          <div className="dt-schema-row">
            <label className="dt-schema-label">Target table</label>
            <select
              className="form-input"
              value={table}
              onChange={(e) => setTable(e.target.value)}
            >
              {tables.length === 0 && <option value="">— no tables yet —</option>}
              {tables.map((t) => (
                <option key={t.path} value={t.path}>{t.title} ({t.path})</option>
              ))}
            </select>
          </div>
        )}

        {kind === "chat" && (
          <div className="dt-schema-row">
            <label className="dt-schema-label">Prompt</label>
            <textarea
              className="form-input form-textarea"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Add a new customer named {name}"
              rows={3}
            />
          </div>
        )}

        {kind === "form" && (
          <div className="dt-prefill-section">
            <div className="dt-prefill-header">
              <label className="dt-schema-label">Pre-fill (optional)</label>
              <p className="dt-prefill-hint">
                Fill in any fields you want pre-filled when this action runs.
                Leave blank to ask each time.
              </p>
            </div>
            {schemaLoading && <div className="dt-loading">Loading table…</div>}
            {schemaError && <div className="dt-error">{schemaError}</div>}
            {!schemaLoading && !schemaError && prefillFields.length > 0 && (
              <PrefillCapture
                key={table /* reset state when target changes */}
                fields={prefillFields}
                hostPath={table}
                values={prefill}
                onChange={setPrefill}
              />
            )}
          </div>
        )}

        {error && <div className="dt-error" style={{ marginTop: 8 }}>{error}</div>}

        <div className="dt-modal-actions">
          <button className="approval-btn" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="approval-btn approval-btn-allow"
            type="button"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || saving}
          >
            {saving ? "Saving…" : "Add action"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface PrefillCaptureProps {
  fields: FieldSchema[];
  hostPath: string;
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

/**
 * FormRenderer-backed capture for prefill defaults.
 *
 * Renders the actual target-table form so the user can fill the fields they
 * want pre-set when this action runs. The modal's primary action button
 * (above) saves the operation, so FormRenderer's own actions row is hidden.
 */
function PrefillCapture({ fields, hostPath, values, onChange }: PrefillCaptureProps) {
  return (
    <div className="dt-prefill-form">
      <FormRenderer
        fields={fields}
        initialValues={values}
        hostPath={hostPath}
        hideActions
        onChange={onChange}
        onSubmit={onChange}
      />
    </div>
  );
}
