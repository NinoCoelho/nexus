import { useCallback, useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import type { DashboardFlow } from "../../api/dashboard";
import {
  getVaultDataTable,
  addVaultDataTableRow,
  bulkAddVaultDataTableRows,
  type DataTable,
} from "../../api/datatable";
import { useToast } from "../../toast/ToastProvider";
import "./FlowStepper.css";

interface Props {
  flow: DashboardFlow;
  folder: string;
  onClose: () => void;
  onComplete: () => void;
}

interface ParentRef {
  step: number;
  field: string;
}

interface StepDef {
  type: string;
  table?: string;
  fields?: string[];
  prefill?: Record<string, unknown>;
  message?: string;
  parent_ref?: ParentRef;
}

function resolveTablePath(folder: string, table: string | undefined): string {
  if (!table) return "";
  if (table.startsWith("./")) return `${folder}/${table.slice(2)}`;
  if (table.includes("/")) return table;
  return `${folder}/${table}`;
}

export default function FlowStepper({ flow, folder, onClose, onComplete }: Props) {
  const toast = useToast();
  const steps = (flow.steps ?? []) as unknown as StepDef[];
  const [currentStep, setCurrentStep] = useState(0);
  const [createdIds, setCreatedIds] = useState<Record<number, string>>({});
  const [tableCache, setTableCache] = useState<Record<string, DataTable>>({});
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [repeatableRows, setRepeatableRows] = useState<Record<string, unknown>[]>([{}]);
  const [submitting, setSubmitting] = useState(false);

  const step = steps[currentStep];
  const isLast = currentStep === steps.length - 1;
  const isFirst = currentStep === 0;

  const tablePath = useMemo(
    () => resolveTablePath(folder, step?.table),
    [step?.table, folder],
  );

  useEffect(() => {
    if (!tablePath || tableCache[tablePath]) return;
    let cancelled = false;
    getVaultDataTable(tablePath)
      .then((tbl) => {
        if (!cancelled) setTableCache((prev) => ({ ...prev, [tablePath]: tbl }));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [tablePath, tableCache]);

  useEffect(() => {
    if (step?.prefill) {
      setValues({ ...step.prefill });
    } else {
      setValues({});
    }
    if (step?.type === "repeatable-form") {
      setRepeatableRows([{}]);
    }
  }, [currentStep, step?.prefill, step?.type]);

  const tbl = tablePath ? tableCache[tablePath] : null;
  const schemaFields = tbl?.schema?.fields ?? [];
  const stepFields = step?.fields ?? schemaFields.map((f) => f.name);

  const parentRef = step?.parent_ref;
  const parentRefValue = parentRef ? createdIds[parentRef.step] : undefined;

  const handleNext = useCallback(async () => {
    if (!step) return;

    if (step.type === "form" && tablePath) {
      const row = { ...values };
      if (parentRef && parentRefValue) {
        row[parentRef.field] = parentRefValue;
      }
      const added = await addVaultDataTableRow(tablePath, row);
      const rowId = String(added?._id ?? added?.[tbl?.schema?.table?.primary_key ?? "_id"] ?? "");
      if (rowId) {
        setCreatedIds((prev) => ({ ...prev, [currentStep]: rowId }));
      }
    }

    if (step.type === "repeatable-form" && tablePath) {
      const rows = repeatableRows.map((r) => {
        const row = { ...r };
        if (parentRef && parentRefValue) {
          row[parentRef.field] = parentRefValue;
        }
        return row;
      });
      await bulkAddVaultDataTableRows(tablePath, rows);
    }

    if (isLast) {
      setSubmitting(true);
      try {
        toast.success(`Flow "${flow.name}" completed`);
        onComplete();
      } finally {
        setSubmitting(false);
      }
    } else {
      setCurrentStep((s) => s + 1);
    }
  }, [
    step, tablePath, values, repeatableRows, parentRef, parentRefValue,
    currentStep, isLast, flow.name, toast, onComplete, tbl,
  ]);

  const handleBack = useCallback(() => {
    if (isFirst) {
      onClose();
    } else {
      setCurrentStep((s) => s - 1);
    }
  }, [isFirst, onClose]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
  }

  const repeatableSchema = step?.type === "repeatable-form"
    ? stepFields.map((fn) => schemaFields.find((f) => f.name === fn)).filter(Boolean)
    : [];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="flow-stepper" onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
        <div className="flow-stepper-header">
          <div className="flow-stepper-title">{flow.name}</div>
          <div className="flow-stepper-steps">
            Step {currentStep + 1} of {steps.length}
          </div>
          <button className="flow-stepper-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="flow-stepper-progress">
          {steps.map((_, i) => (
            <div
              key={i}
              className={`flow-stepper-dot${i === currentStep ? " flow-stepper-dot--active" : ""}${i < currentStep ? " flow-stepper-dot--done" : ""}`}
            />
          ))}
        </div>

        <div className="flow-stepper-body">
          {step?.type === "confirm" && (
            <div className="flow-stepper-confirm">
              <p>{step.message ?? "Are you sure?"}</p>
            </div>
          )}

          {step?.type === "form" && (
            <div className="flow-stepper-form">
              {stepFields.map((fieldName) => {
                const fieldDef = schemaFields.find((f) => f.name === fieldName);
                return (
                  <FormField
                    key={fieldName}
                    fieldDef={fieldDef}
                    fieldName={fieldName}
                    value={values[fieldName]}
                    onChange={(v) => setValues((prev) => ({ ...prev, [fieldName]: v }))}
                  />
                );
              })}
            </div>
          )}

          {step?.type === "repeatable-form" && (
            <div className="flow-stepper-repeatable">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--fg-faint)" }}>
                  {repeatableRows.length} item{repeatableRows.length === 1 ? "" : "s"}
                </span>
                <button
                  type="button"
                  className="screen-action-btn screen-action-btn--primary"
                  onClick={() => setRepeatableRows((prev) => [...prev, {}])}
                >
                  + Add Another
                </button>
              </div>
              {repeatableRows.map((row, idx) => (
                <div key={idx} className="flow-stepper-repeatable-row">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: "var(--fg)" }}>
                      Item {idx + 1}
                    </span>
                    {repeatableRows.length > 1 && (
                      <button
                        type="button"
                        className="screen-action-btn"
                        style={{ fontSize: 11, padding: "2px 6px", color: "var(--bad)" }}
                        onClick={() => setRepeatableRows((prev) => prev.filter((_, i) => i !== idx))}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <div className="flow-stepper-form">
                    {repeatableSchema.map((fieldDef) => {
                      if (!fieldDef) return null;
                      const fn = fieldDef.name;
                      if (parentRef && fn === parentRef.field) return null;
                      return (
                        <FormField
                          key={`${idx}-${fn}`}
                          fieldDef={fieldDef}
                          fieldName={fn}
                          value={row[fn]}
                          onChange={(v) =>
                            setRepeatableRows((prev) =>
                              prev.map((r, i) => (i === idx ? { ...r, [fn]: v } : r)),
                            )
                          }
                        />
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

          {step?.type === "search" && (
            <div className="flow-stepper-search">
              <p style={{ color: "var(--fg-faint)", fontSize: 13 }}>{step.message ?? "Search and select a record"}</p>
            </div>
          )}

          {!step && (
            <div className="flow-stepper-empty">
              <p>This flow has no steps defined.</p>
            </div>
          )}
        </div>

        <div className="flow-stepper-actions">
          <button className="modal-btn" onClick={handleBack}>
            {isFirst ? "Cancel" : "Back"}
          </button>
          <button
            className="modal-btn modal-btn--primary"
            onClick={handleNext}
            disabled={submitting}
          >
            {submitting ? "Processing..." : isLast ? "Finish" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface FormFieldProps {
  fieldDef: { name: string; kind?: string; label?: string } | undefined;
  fieldName: string;
  value: unknown;
  onChange: (v: unknown) => void;
}

function FormField({ fieldDef, fieldName, value, onChange }: FormFieldProps) {
  const kind = fieldDef?.kind ?? "text";
  const label = fieldDef?.label ?? fieldName;

  if (kind === "textarea") {
    return (
      <div className="form-field">
        <label className="form-label">{label}</label>
        <textarea
          className="form-input form-textarea"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
        />
      </div>
    );
  }
  if (kind === "number") {
    return (
      <div className="form-field">
        <label className="form-label">{label}</label>
        <input
          type="number"
          className="form-input"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value === "" ? "" : parseFloat(e.target.value))}
        />
      </div>
    );
  }
  if (kind === "date") {
    return (
      <div className="form-field">
        <label className="form-label">{label}</label>
        <input
          type="date"
          className="form-input"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }
  if (kind === "boolean") {
    return (
      <div className="form-field">
        <label className="form-checkbox-label">
          <input
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{label}</span>
        </label>
      </div>
    );
  }
  return (
    <div className="form-field">
      <label className="form-label">{label}</label>
      <input
        type="text"
        className="form-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
