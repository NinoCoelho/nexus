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

import { useCallback, useEffect, useState } from "react";
import FormRenderer from "../FormRenderer";
import Modal, { type ModalProps } from "../Modal";
import SchemaEditor from "../datatable/SchemaEditor";
import { getVaultDataTable } from "../../api";
import DataTableToolbar from "./DataTableToolbar";
import DataTableGrid from "./DataTableGrid";
import { useDataTableActions } from "./useDataTableActions";
import "../DataTableView.css";

interface Props {
  path: string;
}

type RowRecord = Record<string, unknown>;
const PAGE_SIZE = 25;

export default function DataTableView({ path }: Props) {
  const [table, setTable] = useState<import("../../api").DataTable | null>(null);
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

  const actions = useDataTableActions({
    path, table, search, sort, hidden, activeView,
    reload, setError, setEditingRow, setShowAddForm, setShowSchemaEditor,
    setSearch, setSort, setHidden, setPage, setActiveView, setConfirmModal,
  });

  const { fields, views, visibleFields, sorted, pageCount } = actions;

  if (error) return <div className="dt-error">{error}</div>;
  if (!table) return <div className="dt-loading">Loading…</div>;

  const safePage = Math.min(page, pageCount - 1);
  const pageRows = sorted.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

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
          onSave={actions.saveSchema}
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
        onApplyView={actions.applyView}
        onClearView={() => { setActiveView(""); setSearch(""); setSort(null); setHidden(new Set()); }}
        onSaveView={actions.saveCurrentAsView}
        onDeleteView={() => actions.deleteView(activeView)}
        onToggleHidden={(name) => setHidden((prev) => {
          const next = new Set(prev);
          if (next.has(name)) next.delete(name);
          else next.add(name);
          return next;
        })}
        onExportCSV={actions.exportCSV}
        onImportCSV={actions.importCSV}
        onOpenSchema={() => setShowSchemaEditor(true)}
        onAddRow={() => { setShowAddForm(true); setEditingRow(null); }}
      />

      {showAddForm && (
        <div className="dt-form-panel">
          <div className="dt-form-heading">New Row</div>
          <FormRenderer
            fields={fields.filter((f) => f.kind !== "formula")}
            onSubmit={(v) => void actions.handleAdd(v)}
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
            onSubmit={(v) => void actions.handleUpdate(editingRow, v)}
            onCancel={() => setEditingRow(null)}
            submitLabel="Save"
          />
        </div>
      )}

      <DataTableGrid
        visibleFields={visibleFields}
        pageRows={pageRows}
        sorted={sorted}
        rows={actions.rows}
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
        onCommitEdit={() => void actions.commitInlineEdit(editingCell, cellDraft, () => setEditingCell(null))}
        onCancelEdit={() => setEditingCell(null)}
        onEditRow={(row) => { setEditingRow(row); setShowAddForm(false); }}
        onDeleteRow={actions.handleDelete}
        onPageChange={setPage}
      />
    </div>
  );
}
