/**
 * SchemaEditor — modal UI for editing a data-table's schema.
 *
 * Add/remove/rename columns and change field kinds. Saves via PUT
 * /vault/datatable/schema. Existing rows are preserved by the backend;
 * renames here are *not* migrated — the new column appears empty and
 * the old key is left orphaned in row YAML. Tradeoff for simplicity.
 */

import { useState } from "react";
import type { FieldKind, FieldSchema } from "../../types/form";

interface Props {
  initialTitle?: string;
  initialFields: FieldSchema[];
  onSave: (title: string, fields: FieldSchema[]) => void | Promise<void>;
  onCancel: () => void;
}

const KINDS: FieldKind[] = [
  "text", "textarea", "number", "boolean",
  "select", "multiselect", "date", "vault-link", "formula",
];

export default function SchemaEditor({ initialTitle, initialFields, onSave, onCancel }: Props) {
  const [title, setTitle] = useState(initialTitle ?? "");
  const [fields, setFields] = useState<FieldSchema[]>(() =>
    initialFields.map((f) => ({ ...f })),
  );
  const [saving, setSaving] = useState(false);

  function update(idx: number, patch: Partial<FieldSchema>) {
    setFields((arr) => arr.map((f, i) => (i === idx ? { ...f, ...patch } : f)));
  }
  function remove(idx: number) {
    setFields((arr) => arr.filter((_, i) => i !== idx));
  }
  function move(idx: number, dir: -1 | 1) {
    setFields((arr) => {
      const next = [...arr];
      const j = idx + dir;
      if (j < 0 || j >= next.length) return arr;
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  }
  function add() {
    setFields((arr) => [...arr, { name: `col${arr.length + 1}`, kind: "text" }]);
  }

  async function handleSave() {
    const cleaned = fields
      .filter((f) => f.name.trim())
      .map((f) => ({ ...f, name: f.name.trim() }));
    setSaving(true);
    try {
      await onSave(title.trim(), cleaned);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div className="dt-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dt-modal-title">Edit schema</div>

        <div className="dt-schema-row">
          <label className="dt-schema-label">Title</label>
          <input
            className="form-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Table title"
          />
        </div>

        <div className="dt-schema-fields">
          <div className="dt-schema-fields-head">
            <span>Name</span>
            <span>Label</span>
            <span>Kind</span>
            <span>Choices / Formula</span>
            <span>Req</span>
            <span></span>
          </div>
          {fields.map((f, i) => (
            <div key={i} className="dt-schema-field">
              <input
                className="form-input"
                value={f.name}
                onChange={(e) => update(i, { name: e.target.value })}
                placeholder="name"
              />
              <input
                className="form-input"
                value={f.label ?? ""}
                onChange={(e) => update(i, { label: e.target.value })}
                placeholder="(label)"
              />
              <select
                className="form-input"
                value={f.kind ?? "text"}
                onChange={(e) => update(i, { kind: e.target.value as FieldKind })}
              >
                {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              {f.kind === "formula" ? (
                <input
                  className="form-input"
                  value={f.formula ?? ""}
                  onChange={(e) => update(i, { formula: e.target.value })}
                  placeholder="e.g. price * qty"
                />
              ) : (f.kind === "select" || f.kind === "multiselect") ? (
                <input
                  className="form-input"
                  value={(f.choices ?? []).join(",")}
                  onChange={(e) => update(i, {
                    choices: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                  })}
                  placeholder="a,b,c"
                />
              ) : (
                <input className="form-input" disabled value="" />
              )}
              <input
                type="checkbox"
                checked={!!f.required}
                onChange={(e) => update(i, { required: e.target.checked })}
              />
              <div className="dt-schema-field-actions">
                <button className="dt-action-btn" type="button" onClick={() => move(i, -1)}>↑</button>
                <button className="dt-action-btn" type="button" onClick={() => move(i, 1)}>↓</button>
                <button
                  className="dt-action-btn dt-action-btn--delete"
                  type="button"
                  onClick={() => remove(i)}
                >
                  ×
                </button>
              </div>
            </div>
          ))}
        </div>

        <button className="vault-pill" type="button" onClick={add}>
          + Add column
        </button>

        <div className="dt-modal-actions">
          <button className="approval-btn" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="approval-btn approval-btn-allow"
            type="button"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save schema"}
          </button>
        </div>
      </div>
    </div>
  );
}
