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
import RelatedRowsPanel from "./RelatedRowsPanel";
import { deriveLabelInfo, suggestNextPk } from "../datatable/refOptions";
import { useDataTableActions } from "./useDataTableActions";
import { useVaultLinkPreview, VaultLinkPreviewProvider } from "../vaultLink";
import "../DataTableView.css";

interface Props {
  path: string;
  /** When set, related-row "Open table" buttons call this with another path. */
  onOpenTable?: (path: string) => void;
}

type RowRecord = Record<string, unknown>;
const PAGE_SIZE = 25;

export default function DataTableView({ path, onOpenTable }: Props) {
  const [table, setTable] = useState<import("../../api").DataTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingRow, setEditingRow] = useState<RowRecord | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [showSchemaEditor, setShowSchemaEditor] = useState(false);
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);
  const { onPreview: onVaultPreview, modal: vaultPreviewModal } = useVaultLinkPreview();

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
    <VaultLinkPreviewProvider onPreview={onVaultPreview}>
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

      {showAddForm && (() => {
        // Pre-fill the primary-key column with a suggested next id (e.g.
        // following C001 C002 C003 with C004) so the user doesn't have to
        // type it. Only applied on Add — Edit never overwrites existing PKs.
        const { pkName } = deriveLabelInfo(fields, table.schema.table);
        const next = suggestNextPk(actions.rows, pkName);
        const initialValues = next ? { [pkName]: next } : undefined;
        return (
          <div className="dt-form-panel">
            <div className="dt-form-heading">New Row</div>
            <FormRenderer
              hostPath={path}
              fields={fields.filter((f) => f.kind !== "formula")}
              initialValues={initialValues}
              onSubmit={(v) => void actions.handleAdd(v)}
              onCancel={() => setShowAddForm(false)}
              submitLabel="Add"
            />
          </div>
        );
      })()}

      {editingRow && (() => {
        // Resolve the row's "natural" id the same way the picker + popup do —
        // explicit primary_key wins, otherwise infer from the first
        // required text/number field. Falling back to `_id` (the auto-hash)
        // would mean inbound refs never match because they store the
        // user-facing key (e.g. "C002"), not the hash.
        const { pkName } = deriveLabelInfo(fields, table.schema.table);
        const editingRowId = editingRow[pkName] ?? editingRow._id;
        return (
          <div className="dt-form-panel">
            <div className="dt-form-heading">Edit Row</div>
            <FormRenderer
              hostPath={path}
              fields={fields.filter((f) => f.kind !== "formula")}
              initialValues={editingValues}
              onSubmit={(v) => void actions.handleUpdate(editingRow, v)}
              onCancel={() => setEditingRow(null)}
              submitLabel="Save"
            />
            {editingRowId !== undefined && editingRowId !== "" && (
              <RelatedRowsPanel
                path={path}
                rowId={String(editingRowId)}
                onOpenTable={onOpenTable}
              />
            )}
          </div>
        );
      })()}

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
        hostPath={path}
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
      {vaultPreviewModal}
    </div>
    </VaultLinkPreviewProvider>
  );
}
