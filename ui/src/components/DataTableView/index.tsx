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

import { useCallback, useEffect, useMemo, useState } from "react";
import FormRenderer from "../FormRenderer";
import Modal, { type ModalProps } from "../Modal";
import SchemaEditor from "../datatable/SchemaEditor";
import { evalFormula } from "../datatable/formula";
import { downloadCSV, parseCSV, toCSV } from "../datatable/csv";
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
} from "../../api";
import type { FieldSchema } from "../../types/form";
import DataTableToolbar from "./DataTableToolbar";
import DataTableGrid from "./DataTableGrid";
import { cmp, coerceCSVValue, stripFormulas } from "./utils";
import "../DataTableView.css";

interface Props {
  path: string;
}

type RowRecord = Record<string, unknown>;
const PAGE_SIZE = 25;

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

  const reload = useCallback(() => {
    setError(null);
    getVaultDataTable(path)
      .then(setTable)
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load table"),
      );
  }, [path]);

  useEffect(() => { reload(); }, [reload]);

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
        if (csvRows.length === 0) { setError("CSV had no data rows"); return; }
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

      <DataTableToolbar
        title={table.schema?.title}
        fields={fields}
        views={views}
        search={search}
        activeView={activeView}
        hidden={hidden}
        onSearchChange={setSearch}
        onApplyView={applyView}
        onClearView={() => { setActiveView(""); setSearch(""); setSort(null); setHidden(new Set()); }}
        onSaveView={saveCurrentAsView}
        onDeleteView={() => deleteView(activeView)}
        onToggleHidden={(name) => setHidden((prev) => {
          const next = new Set(prev);
          if (next.has(name)) next.delete(name);
          else next.add(name);
          return next;
        })}
        onExportCSV={exportCSV}
        onImportCSV={importCSV}
        onOpenSchema={() => setShowSchemaEditor(true)}
        onAddRow={() => { setShowAddForm(true); setEditingRow(null); }}
      />

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

      <DataTableGrid
        visibleFields={visibleFields}
        pageRows={pageRows}
        sorted={sorted}
        rows={rows}
        fields={fields}
        sort={sort}
        editingCell={editingCell}
        cellDraft={cellDraft}
        safePage={safePage}
        pageCount={pageCount}
        onToggleSort={(name) => setSort((s) => {
          if (!s || s.field !== name) return { field: name, dir: "asc" };
          if (s.dir === "asc") return { field: name, dir: "desc" };
          return null;
        })}
        onStartEdit={(rowId, f, value) => { setEditingCell({ rowId, field: f.name }); setCellDraft(value); }}
        onCellDraftChange={setCellDraft}
        onCommitEdit={() => void commitInlineEdit()}
        onCancelEdit={() => setEditingCell(null)}
        onEditRow={(row) => { setEditingRow(row); setShowAddForm(false); }}
        onDeleteRow={handleDelete}
        onPageChange={setPage}
      />
    </div>
  );
}
