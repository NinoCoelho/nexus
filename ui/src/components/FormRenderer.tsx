/**
 * FormRenderer — renders a dynamic form from a FieldSchema array.
 * Used by ApprovalDialog (kind='form') and DataTableView (Add/Edit row).
 *
 * Collects values locally, validates required fields on submit, and
 * calls onSubmit with the final dict. No external lib dependency.
 */

import { FormEvent, useState } from "react";
import type { FieldSchema } from "../types/form";
import { useRefOptions } from "./datatable/refOptions";
import "./FormRenderer.css";

interface Props {
  fields: FieldSchema[];
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  /** Optional: fires after every field change with the latest values. */
  onChange?: (values: Record<string, unknown>) => void;
  onCancel?: () => void;
  submitLabel?: string;
  /** Vault path of the host file. Required for kind="ref" target_table resolution. */
  hostPath?: string;
  /** Hide the form-actions row (Cancel / Submit). The parent owns those. */
  hideActions?: boolean;
}

export default function FormRenderer({
  fields,
  initialValues = {},
  onSubmit,
  onChange,
  onCancel,
  submitLabel = "Submit",
  hostPath = "",
  hideActions = false,
}: Props) {
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {};
    for (const f of fields) {
      if (f.name in initialValues) {
        init[f.name] = initialValues[f.name];
      } else if (f.default !== undefined) {
        init[f.name] = f.default;
      } else if (f.kind === "boolean") {
        init[f.name] = false;
      } else if (f.kind === "multiselect") {
        init[f.name] = [];
      } else {
        init[f.name] = "";
      }
    }
    return init;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  function set(name: string, value: unknown) {
    setValues((v) => {
      const next = { ...v, [name]: value };
      // Fire onChange synchronously with the freshest snapshot so callers
      // that need live values (e.g. AddOperationModal's prefill capture)
      // don't drift one keystroke behind.
      onChange?.(next);
      return next;
    });
    setErrors((e) => {
      const next = { ...e };
      delete next[name];
      return next;
    });
  }

  function validate(): boolean {
    const errs: Record<string, string> = {};
    for (const f of fields) {
      if (!f.required) continue;
      const val = values[f.name];
      if (
        val === undefined ||
        val === null ||
        val === "" ||
        (Array.isArray(val) && val.length === 0)
      ) {
        errs[f.name] = `${f.label ?? f.name} is required`;
      }
    }
    setErrors(errs);
    return Object.keys(errs).length === 0;
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    onSubmit(values);
  }

  return (
    <form className="form-renderer" onSubmit={handleSubmit}>
      {fields.map((f) => (
        <div key={f.name} className="form-field">
          <label className="form-label">
            {f.label ?? f.name}
            {f.required && <span className="form-required"> *</span>}
          </label>
          {(f.help || f.help_url) && (
            <p className="form-help">
              {f.help}
              {f.help_url && (
                <>
                  {f.help ? " " : ""}
                  <a href={f.help_url} target="_blank" rel="noreferrer">
                    Get it here →
                  </a>
                </>
              )}
            </p>
          )}
          <FieldInput field={f} value={values[f.name]} onChange={(v) => set(f.name, v)} hostPath={hostPath} />
          {errors[f.name] && <p className="form-error">{errors[f.name]}</p>}
        </div>
      ))}
      {!hideActions && (
        <div className="form-actions">
          {onCancel && (
            <button type="button" className="approval-btn" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button type="submit" className="approval-btn approval-btn-allow">
            {submitLabel}
          </button>
        </div>
      )}
    </form>
  );
}

interface FieldInputProps {
  field: FieldSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  hostPath?: string;
}

function RefFieldInput({ field, value, onChange, hostPath }: FieldInputProps) {
  const cardinality = field.cardinality ?? "one";
  const { options, error } = useRefOptions(field, hostPath ?? "");
  if (cardinality === "many") {
    const arr = Array.isArray(value) ? (value as unknown[]) : value ? [value] : [];
    return (
      <input
        type="text"
        className="form-input"
        value={arr.map(String).join(", ")}
        onChange={(e) =>
          onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
        }
        placeholder="comma-separated IDs"
      />
    );
  }
  if (options === null) {
    return <input className="form-input" disabled value="loading…" />;
  }
  if (error) {
    return (
      <input
        type="text"
        className="form-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        title={`target load failed: ${error}`}
        placeholder="paste target row id"
      />
    );
  }
  return (
    <select
      className="form-input"
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">—</option>
      {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  );
}

function FieldInput({ field, value, onChange, hostPath }: FieldInputProps) {
  const kind = field.kind ?? "text";

  if (kind === "ref") {
    return <RefFieldInput field={field} value={value} onChange={onChange} hostPath={hostPath} />;
  }

  // Masked secret field — short-circuits before kind-specific branches so a
  // `secret: true` text field always renders as type=password.
  if (field.secret) {
    return (
      <input
        type="password"
        className="form-input"
        value={String(value ?? "")}
        placeholder={field.placeholder ?? ""}
        autoComplete="new-password"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (kind === "boolean") {
    return (
      <label className="form-checkbox-label">
        <input
          type="checkbox"
          checked={!!value}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>{field.placeholder ?? ""}</span>
      </label>
    );
  }

  if (kind === "select" && field.choices) {
    return (
      <select
        className="form-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{field.placeholder ?? "Select…"}</option>
        {field.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  }

  if (kind === "multiselect" && field.choices) {
    const selected = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="form-multiselect">
        {field.choices.map((c) => (
          <label key={c} className="form-checkbox-label">
            <input
              type="checkbox"
              checked={selected.includes(c)}
              onChange={(e) => {
                if (e.target.checked) {
                  onChange([...selected, c]);
                } else {
                  onChange(selected.filter((s) => s !== c));
                }
              }}
            />
            <span>{c}</span>
          </label>
        ))}
      </div>
    );
  }

  if (kind === "textarea") {
    return (
      <textarea
        className="form-input form-textarea"
        value={String(value ?? "")}
        placeholder={field.placeholder ?? ""}
        onChange={(e) => onChange(e.target.value)}
        rows={4}
      />
    );
  }

  if (kind === "number") {
    return (
      <input
        type="number"
        className="form-input"
        value={String(value ?? "")}
        placeholder={field.placeholder ?? ""}
        onChange={(e) =>
          onChange(e.target.value === "" ? "" : parseFloat(e.target.value))
        }
      />
    );
  }

  if (kind === "date") {
    return (
      <input
        type="date"
        className="form-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (kind === "formula") {
    return (
      <input
        type="text"
        className="form-input"
        value={String(value ?? "")}
        readOnly
        placeholder={field.formula ? `= ${field.formula}` : "(computed)"}
        title="Formula field — value computed from other fields"
      />
    );
  }

  if (kind === "vault-link") {
    return (
      <input
        type="text"
        className="form-input"
        value={String(value ?? "")}
        placeholder={field.placeholder ?? "vault path (e.g. notes/foo.md)"}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  // default: text
  return (
    <input
      type="text"
      className="form-input"
      value={String(value ?? "")}
      placeholder={field.placeholder ?? ""}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
