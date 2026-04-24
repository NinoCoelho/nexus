/**
 * FormRenderer — renders a dynamic form from a FieldSchema array.
 * Used by ApprovalDialog (kind='form') and DataTableView (Add/Edit row).
 *
 * Collects values locally, validates required fields on submit, and
 * calls onSubmit with the final dict. No external lib dependency.
 */

import { FormEvent, useState } from "react";
import type { FieldSchema } from "../types/form";
import "./FormRenderer.css";

interface Props {
  fields: FieldSchema[];
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  onCancel?: () => void;
  submitLabel?: string;
}

export default function FormRenderer({
  fields,
  initialValues = {},
  onSubmit,
  onCancel,
  submitLabel = "Submit",
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
    setValues((v) => ({ ...v, [name]: value }));
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
          {f.help && <p className="form-help">{f.help}</p>}
          <FieldInput field={f} value={values[f.name]} onChange={(v) => set(f.name, v)} />
          {errors[f.name] && <p className="form-error">{errors[f.name]}</p>}
        </div>
      ))}
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
    </form>
  );
}

interface FieldInputProps {
  field: FieldSchema;
  value: unknown;
  onChange: (v: unknown) => void;
}

function FieldInput({ field, value, onChange }: FieldInputProps) {
  const kind = field.kind ?? "text";

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
