// DataTableView — hook encapsulating all data mutation and derived state logic.

import { useCallback, useEffect, useMemo, useState } from "react";
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
import type { ModalProps } from "../Modal";
import { cmp, coerceCSVValue, stripFormulas } from "./utils";

const PAGE_SIZE = 25;

export interface UseDataTableReturn {
  table: DataTable | null;
  error: string | null;
  fields: FieldSchema[];
  rows: Record<string, unknown>[];
  views: DataTableView[];
  sorted: Record<string, unknown>[];
  pageRows: Record<string, unknown>[];
  pageCount: number;
  safePage: number;
  visibleFields: FieldSchema[];
  confirmModal: ModalProps | null;
  setConfirmModal: (m: ModalProps | null) => void;
  reload: () => void;
  handleAdd: (values: Record<string, unknown>) => Promise<void>;
  handleUpdate: (values: Record<string, unknown>, editingRow: Record<string, unknown>) => Promise<void>;
  handleDelete: (rowId: string) => void;
  commitInlineEdit: (editingCell: { rowId: string; field: string }, cellDraft: unknown) => Promise<void>;
  saveSchema: (title: string, newFields: FieldSchema[]) => Promise<void>;
  exportCSV: (visibleFields: FieldSchema[], sortedRows: Record<string, unknown>[]) => void;
  importCSV: (file: File) => void;
  applyView: (v: DataTableView) => void;
  saveCurrentAsView: (
    search: string,
    sort: { field: string; dir: "asc" | "desc" } | null,
    hidden: Set<string>,
  ) => void;
  deleteView: (name: string) => Promise<void>;
  // state setters exposed for toolbar/table
  search: string;
  setSearch: (v: string) => void;
  sort: { field: string; dir: "asc" | "desc" } | null;
  setSort: React.Dispatch<React.SetStateAction<{ field: string; dir: "asc" | "desc" } | null>>;
  hidden: Set<string>;
  setHidden: React.Dispatch<React.SetStateAction<Set<string>>>;
  page: number;
  setPage: React.Dispatch<React.SetStateAction<number>>;
  activeView: string;
  setActiveView: (v: string) => void;
  showSchemaEditor: boolean;
  setShowSchemaEditor: (v: boolean) => void;
}

export function useDataTable(path: string): UseDataTableReturn {
  const [table, setTable] = useState<DataTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ field: string; dir: "asc" | "desc" } | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const [activeView, setActiveView] = useState<string>("");
  const [showSchemaEditor, setShowSchemaEditor] = useState(false);

  const reload = useCallback(() => {
    setError(null);
    getVaultDataTable(path)
      .then(setTable)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load table"));
  }, [path]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { setPage(0); }, [path, search, sort]);

  const fields: FieldSchema[] = table?.schema?.fields ?? [];
  const rows = table?.rows ?? [];
  const views = table?.views ?? [];
  const visibleFields = fields.filter((f) => !hidden.has(f.name));

  const enriched = useMemo(() =>
    rows.map((r) => {
      const out = { ...r };
      for (const f of fields) {
        if (f.kind === "formula" && f.formula) out[f.name] = evalFormula(f.formula, out);
      }
      return out;
    }), [rows, fields]);

  const searched = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return enriched;
    return enriched.filter((r) =>
      fields.some((f) => { const v = r[f.name]; if (v == null) return false; return String(v).toLowerCase().includes(q); }),
    );
  }, [enriched, search, fields]);

  const sorted = useMemo(() => {
    if (!sort) return searched;
    const copy = [...searched];
    copy.sort((a, b) => cmp(a[sort.field], b[sort.field]) * (sort.dir === "asc" ? 1 : -1));
    return copy;
  }, [searched, sort]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = sorted.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  async function handleAdd(values: Record<string, unknown>) {
    try {
      await addVaultDataTableRow(path, stripFormulas(values, fields));
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Add failed"); }
  }

  async function handleUpdate(values: Record<string, unknown>, editingRow: Record<string, unknown>) {
    const rowId = String(editingRow._id ?? "");
    try {
      await updateVaultDataTableRow(path, rowId, stripFormulas(values, fields));
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Update failed"); }
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
        try { await deleteVaultDataTableRow(path, rowId); reload(); }
        catch (e) { setError(e instanceof Error ? e.message : "Delete failed"); }
      },
    });
  }

  async function commitInlineEdit(editingCell: { rowId: string; field: string }, cellDraft: unknown) {
    const { rowId, field } = editingCell;
    try {
      await updateVaultDataTableRow(path, rowId, { [field]: cellDraft });
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Update failed"); }
  }

  async function saveSchema(title: string, newFields: FieldSchema[]) {
    try {
      await setVaultDataTableSchema(path, { title: title || undefined, fields: newFields });
      setShowSchemaEditor(false);
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Schema save failed"); }
  }

  function exportCSV(vis: FieldSchema[], sortedRows: Record<string, unknown>[]) {
    const headers = vis.map((f) => f.name);
    const csv = toCSV(headers, sortedRows);
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
      } catch (e) { setError(e instanceof Error ? e.message : "Import failed"); }
    };
    reader.readAsText(file);
  }

  function applyView(v: DataTableView) {
    setActiveView(v.name);
    setSearch(v.filter ?? "");
    setSort(v.sort ?? null);
    setHidden(new Set(v.hidden ?? []));
    setPage(0);
  }

  function saveCurrentAsView(
    currentSearch: string,
    currentSort: { field: string; dir: "asc" | "desc" } | null,
    currentHidden: Set<string>,
  ) {
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
          filter: currentSearch || undefined,
          sort: currentSort ?? undefined,
          hidden: currentHidden.size > 0 ? Array.from(currentHidden) : undefined,
        };
        const next = [...views.filter((v) => v.name !== view.name), view];
        try {
          await setVaultDataTableViews(path, next);
          setActiveView(view.name);
          reload();
        } catch (e) { setError(e instanceof Error ? e.message : "View save failed"); }
      },
    });
  }

  async function deleteView(name: string) {
    const next = views.filter((v) => v.name !== name);
    try {
      await setVaultDataTableViews(path, next);
      if (activeView === name) setActiveView("");
      reload();
    } catch (e) { setError(e instanceof Error ? e.message : "View delete failed"); }
  }

  return {
    table, error, fields, rows, views, sorted, pageRows, pageCount, safePage,
    visibleFields, confirmModal, setConfirmModal, reload,
    handleAdd, handleUpdate, handleDelete, commitInlineEdit,
    saveSchema, exportCSV, importCSV, applyView, saveCurrentAsView, deleteView,
    search, setSearch, sort, setSort, hidden, setHidden, page, setPage,
    activeView, setActiveView, showSchemaEditor, setShowSchemaEditor,
  };
}
