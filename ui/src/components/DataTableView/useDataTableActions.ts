// Data mutation and view management actions extracted from DataTableView/index.tsx.

// No react hooks are called here — this is a plain action factory.
import type { ModalProps } from "../Modal";
import { evalFormula } from "../datatable/formula";
import { downloadCSV, parseCSV, toCSV } from "../datatable/csv";
import {
  addVaultDataTableRow,
  bulkAddVaultDataTableRows,
  deleteVaultDataTableRow,
  setVaultDataTableSchema,
  setVaultDataTableViews,
  updateVaultDataTableRow,
  type DataTable,
  type DataTableView,
} from "../../api";
import type { FieldSchema } from "../../types/form";
import { cmp, coerceCSVValue, stripFormulas } from "./utils";


type RowRecord = Record<string, unknown>;
const PAGE_SIZE = 25;

interface UseDataTableActionsOptions {
  path: string;
  table: DataTable | null;
  search: string;
  sort: { field: string; dir: "asc" | "desc" } | null;
  hidden: Set<string>;
  activeView: string;
  reload: () => void;
  setError: (msg: string | null) => void;
  setEditingRow: (row: RowRecord | null) => void;
  setShowAddForm: (show: boolean) => void;
  setShowSchemaEditor: (show: boolean) => void;
  setSearch: (s: string) => void;
  setSort: (s: { field: string; dir: "asc" | "desc" } | null) => void;
  setHidden: (h: Set<string>) => void;
  setPage: (p: number) => void;
  setActiveView: (v: string) => void;
  setConfirmModal: (m: ModalProps | null) => void;
}

export function useDataTableActions({
  path,
  table,
  search,
  sort,
  hidden,
  activeView,
  reload,
  setError,
  setEditingRow,
  setShowAddForm,
  setShowSchemaEditor,
  setSearch,
  setSort,
  setHidden,
  setPage,
  setActiveView,
  setConfirmModal,
}: UseDataTableActionsOptions) {
  const fields: FieldSchema[] = table?.schema?.fields ?? [];
  const rows = table?.rows ?? [];
  const views = table?.views ?? [];
  const visibleFields = fields.filter((f) => !hidden.has(f.name));

  // Compute enriched rows (formula evaluation)
  const enriched = rows.map((r) => {
    const out = { ...r };
    for (const f of fields) {
      if (f.kind === "formula" && f.formula) {
        out[f.name] = evalFormula(f.formula, out);
      }
    }
    return out;
  });

  // Filter
  const q = search.trim().toLowerCase();
  const searched = q
    ? enriched.filter((r) =>
        fields.some((f) => {
          const v = r[f.name];
          if (v == null) return false;
          return String(v).toLowerCase().includes(q);
        }),
      )
    : enriched;

  // Sort
  const sorted = sort
    ? [...searched].sort((a, b) => cmp(a[sort.field], b[sort.field]) * (sort.dir === "asc" ? 1 : -1))
    : searched;

  // Paginate
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));

  // Mutations
  async function handleAdd(values: RowRecord) {
    try {
      await addVaultDataTableRow(path, stripFormulas(values, fields));
      setShowAddForm(false);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Add failed");
    }
  }

  async function handleUpdate(editingRow: RowRecord | null, values: RowRecord) {
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

  async function commitInlineEdit(editingCell: { rowId: string; field: string } | null, cellDraft: unknown, onCancelEdit: () => void) {
    if (!editingCell) return;
    const { rowId, field } = editingCell;
    onCancelEdit();
    try {
      await updateVaultDataTableRow(path, rowId, { [field]: cellDraft });
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  }

  // Schema editor
  async function saveSchema(title: string, newFields: FieldSchema[]) {
    try {
      await setVaultDataTableSchema(path, { title: title || undefined, fields: newFields });
      setShowSchemaEditor(false);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Schema save failed");
    }
  }

  // CSV
  function exportCSV() {
    const headers = visibleFields.map((f) => f.name);
    const csv = toCSV(headers, sorted as RowRecord[]);
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
          const obj: RowRecord = {};
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

  // Views
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

  return {
    fields, rows, views, visibleFields, enriched, searched, sorted, pageCount,
    handleAdd, handleUpdate, handleDelete, commitInlineEdit,
    saveSchema, exportCSV, importCSV, applyView, saveCurrentAsView, deleteView,
  };
}

