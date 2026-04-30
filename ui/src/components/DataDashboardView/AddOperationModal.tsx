/**
 * AddOperationModal — small form to define a new dashboard operation.
 *
 * Operations are quick actions exposed as chips on the database dashboard.
 * "chat" kind sends a pre-set prompt to the floating chat bubble; "form"
 * kind opens an inline pre-filled add-row modal for a target table.
 */

import { useMemo, useState } from "react";
import type { DashboardOperation, OperationKind } from "../../api/dashboard";
import type { DatabaseTableSummary } from "../../api/datatable";

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

export default function AddOperationModal({ folder: _folder, tables, onSubmit, onCancel }: Props) {
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState<OperationKind>("chat");
  const [table, setTable] = useState(tables[0]?.path ?? "");
  const [prompt, setPrompt] = useState("");
  const [prefillJson, setPrefillJson] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const id = useMemo(() => slug(label || "op"), [label]);
  const canSubmit = label.trim().length > 0 && (kind === "chat" || (kind === "form" && !!table));

  async function handleSubmit() {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      let prefill: Record<string, unknown> | undefined;
      if (kind === "form" && prefillJson.trim()) {
        try {
          prefill = JSON.parse(prefillJson);
        } catch {
          setError("Prefill must be valid JSON.");
          setSaving(false);
          return;
        }
      }
      const op: DashboardOperation = {
        id: `op_${id}`,
        label: label.trim(),
        kind,
        prompt: prompt.trim(),
        ...(kind === "form" ? { table } : {}),
        ...(prefill ? { prefill } : {}),
      };
      await onSubmit(op);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div className="dt-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dt-modal-title">New operation</div>

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
            <option value="chat">Chat — send a prompt to the bubble</option>
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

        <div className="dt-schema-row">
          <label className="dt-schema-label">{kind === "chat" ? "Prompt" : "Prompt (optional)"}</label>
          <textarea
            className="form-input form-textarea"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={kind === "chat" ? "Add a new customer named {name}" : "(unused for form kind)"}
            rows={3}
          />
        </div>

        {kind === "form" && (
          <div className="dt-schema-row">
            <label className="dt-schema-label">Prefill (JSON)</label>
            <input
              className="form-input"
              value={prefillJson}
              onChange={(e) => setPrefillJson(e.target.value)}
              placeholder='{"status": "active"}'
            />
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
            {saving ? "Saving…" : "Add operation"}
          </button>
        </div>
      </div>
    </div>
  );
}
