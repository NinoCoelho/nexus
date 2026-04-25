/**
 * DataTableView — interactive CRUD table for a vault data-table file.
 *
 * Features:
 *   - sort/filter/search + pagination
 *   - inline cell edit for simple kinds; modal form for the rest
 *   - schema editor (add/remove/reorder/rename columns)
 *   - CSV import + export
 *   - saved views (filter/sort/hidden-columns presets, persisted in the file)
 *   - vault-link cells render as clickable vault:// links
 *   - formula cells are computed at render time from other fields
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import FormRenderer from "./FormRenderer";
import Modal, { type ModalProps } from "./Modal";
import SchemaEditor from "./datatable/SchemaEditor";
import { evalFormula } from "./datatable/formula";
import { downloadCSV, parseCSV, toCSV } from "./datatable/csv";
import {
  addVaultDataTableRow,
  bulkAddVaultDataTableRows,
  deleteVaultDataTableRow,
  getVaultDataTable,
  setVaultDataTableSchema,
  setVaultDataTableViews,
  updateVaultDataTableRow,
  type DataTable,
  type DataTableView,
} from "../api";
import type { FieldSchema } from "../types/form";
import "./DataTableView.css";

interface Props {
  path: string;
}

type RowRecord = Record<string, unknown>;

const PAGE_SIZE = 25;
const INLINE_EDITABLE: ReadonlySet<string> = new Set([
  "text", "number", "date", "select", "boolean", "vault-link",
]);

export default function DataTableView({ path }: Props) {
  const [table, setTable] = useState<DataTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingRow, setEditingRow] = useState<RowRecord | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [showSchemaEditor, setShowSchemaEditor] = useState(false);
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);

  // toolbar state
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ field: string; dir: "asc" | "desc" } | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const [activeView, setActiveView] = useState<string>("");

  // inline editing
  const [editingCell, setEditingCell] = useState<{ rowId: string; field: string } | null>(null);
  const [cellDraft, setCellDraft] = useState<unknown>("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const reload = useCallback(() => {
    setError(null);
    getVaultDataTable(path)
      .then(setTable)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load table"),
      );
  }, [path]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Reset paging on filter/search/path change
  useEffect(() => { setPage(0); }, [path, search, sort]);

  const fields: FieldSchema[] = table?.schema?.fields ?? [];
  const rows = table?.rows ?? [];
  const views = table?.views ?? [];
  const visibleFields = fields.filter((f) => !hidden.has(f.name));

  // Compute formula values for every row once, in a derived map.
  const enriched = useMemo(() =>
    rows.map((r) => {
      const out = { ...r };
      for (const f of fields) {
        if (f.kind === "formula" && f.formula) {
          out[f.name] = evalFormula(f.formula, out);
        }
      }
      return out;
    }), [rows, fields]);

  // Filter
  const searched = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return enriched;
    return enriched.filter((r) =>
      fields.some((f) => {
        const v = r[f.name];
        if (v == null) return false;
        return String(v).toLowerCase().includes(q);
      }),
    );
  }, [enriched, search, fields]);

  // Sort
  const sorted = useMemo(() => {
    if (!sort) return searched;
    const copy = [...searched];
    copy.sort((a, b) => cmp(a[sort.field], b[sort.field]) * (sort.dir === "asc" ? 1 : -1));
    return copy;
  }, [searched, sort]);

  if (error) return <div className="dt-error">{error}</div>;
  if (!table) return <div className="dt-loading">Loading…</div>;

  // Paginate
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = sorted.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  // Mutations ───────────────────────────────────────────────────────────
  async function handleAdd(values: Record<string, unknown>) {
    try {
      await addVaultDataTableRow(path, stripFormulas(values, fields));
      setShowAddForm(false);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Add failed");
    }
  }

  async function handleUpdate(values: Record<string, unknown>) {
    if (!editingRow) return;
    const rowId = String(editingRow._id ?? "");
    try {
      await updateVaultDataTableRow(path, rowId, stripFormulas(values, fields));
      setEditingRow(null);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  }

  function handleDelete(rowId: string) {
    setConfirmModal({
      kind: "confirm",
      title: "Delete row",
      message: "This row will be removed from the table.",
      confirmLabel: "Delete",
      danger: true,
      onCancel: () => setConfirmModal(null),
      onSubmit: async () => {
        setConfirmModal(null);
        try {
          await deleteVaultDataTableRow(path, rowId);
          reload();
        } catch (e) {
          setError(e instanceof Error ? e.message : "Delete failed");
        }
      },
    });
  }

  async function commitInlineEdit() {
    if (!editingCell) return;
    const { rowId, field } = editingCell;
    setEditingCell(null);
    try {
      await updateVaultDataTableRow(path, rowId, { [field]: cellDraft });
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  }

  // Schema editor ───────────────────────────────────────────────────────
  async function saveSchema(title: string, newFields: FieldSchema[]) {
    try {
      await setVaultDataTableSchema(path, { title: title || undefined, fields: newFields });
      setShowSchemaEditor(false);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Schema save failed");
    }
  }

  // CSV ─────────────────────────────────────────────────────────────────
  function exportCSV() {
    const headers = visibleFields.map((f) => f.name);
    const csv = toCSV(headers, sorted as Record<string, unknown>[]);
    const filename = (path.split("/").pop() ?? "table").replace(/\.md$/, "") + ".csv";
    downloadCSV(filename, csv);
  }

  function importCSV(file: File) {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const { headers, rows: csvRows } = parseCSV(String(reader.result ?? ""));
        if (csvRows.length === 0) {
          setError("CSV had no data rows");
          return;
        }
        const knownNames = new Set(fields.map((f) => f.name));
        const cleaned = csvRows.map((r) => {
          const obj: Record<string, unknown> = {};
          for (const h of headers) {
            if (knownNames.has(h)) obj[h] = coerceCSVValue(r[h], fields.find((f) => f.name === h));
          }
          return obj;
        });
        await bulkAddVaultDataTableRows(path, cleaned);
        reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Import failed");
      }
    };
    reader.readAsText(file);
  }

  // Views ───────────────────────────────────────────────────────────────
  function applyView(v: DataTableView) {
    setActiveView(v.name);
    setSearch(v.filter ?? "");
    setSort(v.sort ?? null);
    setHidden(new Set(v.hidden ?? []));
    setPage(0);
  }

  function saveCurrentAsView() {
    setConfirmModal({
      kind: "prompt",
      title: "Save view",
      message: "Save current filter/sort/columns as a view called:",
      confirmLabel: "Save",
      onCancel: () => setConfirmModal(null),
      onSubmit: async (name: string) => {
        setConfirmModal(null);
        const view: DataTableView = {
          name: name.trim(),
          filter: search || undefined,
          sort: sort ?? undefined,
          hidden: hidden.size > 0 ? Array.from(hidden) : undefined,
        };
        const next = [...views.filter((v) => v.name !== view.name), view];
        try {
          await setVaultDataTableViews(path, next);
          setActiveView(view.name);
          reload();
        } catch (e) {
          setError(e instanceof Error ? e.message : "View save failed");
        }
      },
    });
  }

  async function deleteView(name: string) {
    const next = views.filter((v) => v.name !== name);
    try {
      await setVaultDataTableViews(path, next);
      if (activeView === name) setActiveView("");
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "View delete failed");
    }
  }

  function toggleHidden(name: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleSort(name: string) {
    setSort((s) => {
      if (!s || s.field !== name) return { field: name, dir: "asc" };
      if (s.dir === "asc") return { field: name, dir: "desc" };
      return null;
    });
  }

  const editingValues = editingRow
    ? Object.fromEntries(fields.map((f) => [f.name, editingRow[f.name]]))
    : undefined;

  return (
    <div className="dt-container">
      {confirmModal && <Modal {...confirmModal} />}
      {showSchemaEditor && (
        <SchemaEditor
          initialTitle={table.schema?.title}
          initialFields={fields}
          onSave={saveSchema}
          onCancel={() => setShowSchemaEditor(false)}
        />
      )}

      <div className="dt-header">
        <span className="dt-title">{table.schema?.title ?? "Data Table"}</span>
        <div className="dt-header-actions">
          <input
            type="search"
            className="dt-search"
            placeholder="Search…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {views.length > 0 && (
            <select
              className="dt-search dt-view-select"
              value={activeView}
              onChange={(e) => {
                const v = views.find((x) => x.name === e.target.value);
                if (v) applyView(v);
                else { setActiveView(""); setSearch(""); setSort(null); setHidden(new Set()); }
              }}
            >
              <option value="">All rows</option>
              {views.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
            </select>
          )}
          <button className="vault-pill" onClick={saveCurrentAsView} title="Save current filter/sort as a view">
            Save view
          </button>
          {activeView && (
            <button
              className="dt-action-btn dt-action-btn--delete"
              onClick={() => deleteView(activeView)}
              title="Delete the active view"
            >
              Del view
            </button>
          )}
          <ColumnVisibilityMenu
            fields={fields}
            hidden={hidden}
            onToggle={toggleHidden}
          />
          <button className="vault-pill" onClick={exportCSV} title="Download as CSV">
            Export
          </button>
          <button
            className="vault-pill"
            onClick={() => fileInputRef.current?.click()}
            title="Import rows from CSV"
          >
            Import
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,text/csv"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importCSV(f);
              e.target.value = "";
            }}
          />
          <button
            className="vault-pill"
            onClick={() => setShowSchemaEditor(true)}
            title="Edit schema"
          >
            Schema
          </button>
          <button
            className="vault-pill"
            onClick={() => { setShowAddForm(true); setEditingRow(null); }}
          >
            + Add Row
          </button>
        </div>
      </div>

      {showAddForm && (
        <div className="dt-form-panel">
          <div className="dt-form-heading">New Row</div>
          <FormRenderer
            fields={fields.filter((f) => f.kind !== "formula")}
            onSubmit={(v) => void handleAdd(v)}
            onCancel={() => setShowAddForm(false)}
            submitLabel="Add"
          />
        </div>
      )}

      {editingRow && (
        <div className="dt-form-panel">
          <div className="dt-form-heading">Edit Row</div>
          <FormRenderer
            fields={fields.filter((f) => f.kind !== "formula")}
            initialValues={editingValues}
            onSubmit={(v) => void handleUpdate(v)}
            onCancel={() => setEditingRow(null)}
            submitLabel="Save"
          />
        </div>
      )}

      {fields.length === 0 ? (
        <div className="dt-empty">
          No columns yet — click <strong>Schema</strong> to add columns.
        </div>
      ) : sorted.length === 0 ? (
        <div className="dt-empty">
          {rows.length === 0
            ? "No rows yet — click + Add Row to start."
            : "No rows match the current filter."}
        </div>
      ) : (
        <div className="dt-table-wrap">
          <table className="dt-table">
            <thead>
              <tr>
                {visibleFields.map((f) => (
                  <th
                    key={f.name}
                    className="dt-th-sortable"
                    onClick={() => toggleSort(f.name)}
                    title="Click to sort"
                  >
                    {f.label ?? f.name}
                    {sort?.field === f.name && (
                      <span className="dt-sort-arrow">{sort.dir === "asc" ? " ▲" : " ▼"}</span>
                    )}
                  </th>
                ))}
                <th className="dt-actions-col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row, i) => {
                const rowId = String(row._id ?? i);
                return (
                  <tr key={rowId}>
                    {visibleFields.map((f) => {
                      const isEditing = editingCell?.rowId === rowId && editingCell.field === f.name;
                      const inlineable = INLINE_EDITABLE.has(f.kind ?? "text") && f.kind !== "formula";
                      return (
                        <td
                          key={f.name}
                          onDoubleClick={() => {
                            if (!inlineable) return;
                            setEditingCell({ rowId, field: f.name });
                            setCellDraft(row[f.name] ?? (f.kind === "boolean" ? false : ""));
                          }}
                          title={inlineable ? "Double-click to edit" : undefined}
                        >
                          {isEditing ? (
                            <InlineEditor
                              field={f}
                              value={cellDraft}
                              onChange={setCellDraft}
                              onCommit={() => void commitInlineEdit()}
                              onCancel={() => setEditingCell(null)}
                            />
                          ) : (
                            renderCell(row[f.name], f)
                          )}
                        </td>
                      );
                    })}
                    <td className="dt-actions-col">
                      <button
                        className="dt-action-btn"
                        onClick={() => { setEditingRow(row); setShowAddForm(false); }}
                        title="Edit"
                      >
                        Edit
                      </button>
                      <button
                        className="dt-action-btn dt-action-btn--delete"
                        onClick={() => handleDelete(rowId)}
                        title="Delete"
                      >
                        Del
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {sorted.length > PAGE_SIZE && (
        <div className="dt-pagination">
          <button
            className="dt-action-btn"
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            ← Prev
          </button>
          <span className="dt-page-info">
            Page {safePage + 1} of {pageCount} · {sorted.length} rows
          </span>
          <button
            className="dt-action-btn"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────

function ColumnVisibilityMenu({
  fields, hidden, onToggle,
}: {
  fields: FieldSchema[];
  hidden: Set<string>;
  onToggle: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dt-cols-menu">
      <button className="vault-pill" onClick={() => setOpen((o) => !o)} title="Show/hide columns">
        Columns ({fields.length - hidden.size}/{fields.length})
      </button>
      {open && (
        <div className="dt-cols-popover" onMouseLeave={() => setOpen(false)}>
          {fields.map((f) => (
            <label key={f.name} className="dt-cols-item">
              <input
                type="checkbox"
                checked={!hidden.has(f.name)}
                onChange={() => onToggle(f.name)}
              />
              <span>{f.label ?? f.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function InlineEditor({
  field, value, onChange, onCommit, onCancel,
}: {
  field: FieldSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const kind = field.kind ?? "text";
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); onCommit(); }
    else if (e.key === "Escape") { e.preventDefault(); onCancel(); }
  };
  if (kind === "boolean") {
    return (
      <input
        autoFocus
        type="checkbox"
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        onBlur={onCommit}
        onKeyDown={onKey}
      />
    );
  }
  if (kind === "select" && field.choices) {
    return (
      <select
        autoFocus
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={onKey}
      >
        <option value="">—</option>
        {field.choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  }
  if (kind === "number") {
    return (
      <input
        autoFocus
        type="number"
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value === "" ? "" : parseFloat(e.target.value))}
        onBlur={onCommit}
        onKeyDown={onKey}
      />
    );
  }
  if (kind === "date") {
    return (
      <input
        autoFocus
        type="date"
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={onKey}
      />
    );
  }
  // text + vault-link
  return (
    <input
      autoFocus
      type="text"
      className="dt-cell-input"
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onCommit}
      onKeyDown={onKey}
    />
  );
}

function renderCell(value: unknown, field: FieldSchema): React.ReactNode {
  if (value === null || value === undefined || value === "") return "";
  const kind = field.kind ?? "text";
  if (kind === "boolean") return value ? "✓" : "";
  if (kind === "vault-link") {
    const v = String(value);
    return <a href={`vault://${v}`}>{v}</a>;
  }
  if (kind === "formula") {
    return <span className="dt-cell-formula">{String(value)}</span>;
  }
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function cmp(a: unknown, b: unknown): number {
  const an = a === null || a === undefined || a === "";
  const bn = b === null || b === undefined || b === "";
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  // try numeric compare on strings that look like numbers
  const ax = typeof a === "string" ? parseFloat(a) : NaN;
  const bx = typeof b === "string" ? parseFloat(b) : NaN;
  if (!Number.isNaN(ax) && !Number.isNaN(bx) && /^-?\d/.test(String(a)) && /^-?\d/.test(String(b))) {
    return ax - bx;
  }
  return String(a).localeCompare(String(b));
}

function stripFormulas(values: Record<string, unknown>, fields: FieldSchema[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const formulaNames = new Set(fields.filter((f) => f.kind === "formula").map((f) => f.name));
  for (const [k, v] of Object.entries(values)) {
    if (!formulaNames.has(k)) out[k] = v;
  }
  return out;
}

function coerceCSVValue(raw: string | undefined, field: FieldSchema | undefined): unknown {
  if (raw === undefined) return "";
  if (!field) return raw;
  const kind = field.kind ?? "text";
  if (kind === "number") {
    const n = parseFloat(raw);
    return Number.isNaN(n) ? "" : n;
  }
  if (kind === "boolean") {
    const v = raw.trim().toLowerCase();
    return v === "true" || v === "1" || v === "yes" || v === "✓";
  }
  if (kind === "multiselect") {
    return raw.split(/[;,]/).map((s) => s.trim()).filter(Boolean);
  }
  return raw;
}
