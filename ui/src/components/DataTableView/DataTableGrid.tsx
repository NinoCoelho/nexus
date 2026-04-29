/**
 * @file DataTableView grid: sortable headers, inline editing, row actions, and pagination.
 *
 * Purely presentational component — receives already-processed data (filtered/sorted rows,
 * current page) and delegates all state logic to the parent via callbacks.
 */

import { useState } from "react";
import type { FieldSchema } from "../../types/form";
import InlineEditor from "./InlineEditor";
import { renderCell } from "./utils";
import { RefPreviewPopup } from "./RefPreviewPopup";

type RowRecord = Record<string, unknown>;

const INLINE_EDITABLE: ReadonlySet<string> = new Set([
  "text", "number", "date", "select", "boolean", "vault-link", "ref",
]);

const PAGE_SIZE = 25;

interface Props {
  visibleFields: FieldSchema[];
  pageRows: RowRecord[];
  sorted: RowRecord[];
  rows: RowRecord[];
  fields: FieldSchema[];
  sort: { field: string; dir: "asc" | "desc" } | null;
  editingCell: { rowId: string; field: string } | null;
  cellDraft: unknown;
  safePage: number;
  pageCount: number;
  hostPath?: string;
  onToggleSort: (name: string) => void;
  onStartEdit: (rowId: string, field: FieldSchema, value: unknown) => void;
  onCellDraftChange: (v: unknown) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onEditRow: (row: RowRecord) => void;
  onDeleteRow: (rowId: string) => void;
  onPageChange: (next: number) => void;
}

/**
 * Data grid with sorting, inline editing, and pagination.
 *
 * Renders the current inline edit state via `InlineEditor` when `editingCell`
 * is non-null. Field types that support inline editing are defined by `INLINE_EDITABLE`;
 * other types trigger `onEditRow` (full-form edit).
 *
 * @param visibleFields - Visible columns in display order.
 * @param pageRows - Rows for the current page (subset of `sorted`).
 * @param sorted - All rows after filtering and sorting (used to compute pagination).
 * @param rows - Complete set of rows before filtering or sorting.
 * @param fields - Full field schema, used by InlineEditor.
 * @param sort - Active sort field and direction; `null` if unsorted.
 * @param editingCell - Cell currently being edited inline; `null` if none.
 * @param cellDraft - Intermediate value of the cell being edited.
 * @param safePage - Current page (already clamped to valid bounds).
 * @param pageCount - Total number of available pages.
 * @param onToggleSort - Toggle sort by field; reverses direction if already active.
 * @param onStartEdit - Begin inline editing of a specific cell.
 * @param onCellDraftChange - Update the draft value of the cell being edited.
 * @param onCommitEdit - Persist the current edit.
 * @param onCancelEdit - Discard the edit without saving.
 * @param onEditRow - Open the full edit form for a row.
 * @param onDeleteRow - Delete a row by its ID.
 * @param onPageChange - Navigate to another page.
 */
export default function DataTableGrid({
  visibleFields, pageRows, sorted, rows, fields, sort,
  editingCell, cellDraft, safePage, pageCount, hostPath,
  onToggleSort, onStartEdit, onCellDraftChange, onCommitEdit, onCancelEdit,
  onEditRow, onDeleteRow, onPageChange,
}: Props) {
  const [refPreview, setRefPreview] = useState<{ target: string; id: string } | null>(null);
  const handleRefClick = (target: string, id: string) => setRefPreview({ target, id });
  if (fields.length === 0) {
    return (
      <div className="dt-empty">
        No columns yet — click <strong>Schema</strong> to add columns.
      </div>
    );
  }
  if (sorted.length === 0) {
    return (
      <div className="dt-empty">
        {rows.length === 0
          ? "No rows yet — click + Add Row to start."
          : "No rows match the current filter."}
      </div>
    );
  }

  return (
    <>
      <div className="dt-table-wrap">
        <table className="dt-table">
          <thead>
            <tr>
              {visibleFields.map((f) => (
                <th
                  key={f.name}
                  className="dt-th-sortable"
                  onClick={() => onToggleSort(f.name)}
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
                          onStartEdit(rowId, f, row[f.name] ?? (f.kind === "boolean" ? false : ""));
                        }}
                        title={inlineable ? "Double-click to edit" : undefined}
                      >
                        {isEditing ? (
                          <InlineEditor
                            field={f}
                            value={cellDraft}
                            hostPath={hostPath}
                            onChange={onCellDraftChange}
                            onCommit={() => void onCommitEdit()}
                            onCancel={onCancelEdit}
                          />
                        ) : (
                          renderCell(row[f.name], f, { onRefClick: handleRefClick, hostPath })
                        )}
                      </td>
                    );
                  })}
                  <td className="dt-actions-col">
                    <button
                      className="dt-action-btn"
                      onClick={() => onEditRow(row)}
                      title="Edit"
                    >
                      Edit
                    </button>
                    <button
                      className="dt-action-btn dt-action-btn--delete"
                      onClick={() => onDeleteRow(rowId)}
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

      {sorted.length > PAGE_SIZE && (
        <div className="dt-pagination">
          <button
            className="dt-action-btn"
            disabled={safePage === 0}
            onClick={() => onPageChange(Math.max(0, safePage - 1))}
          >
            ← Prev
          </button>
          <span className="dt-page-info">
            Page {safePage + 1} of {pageCount} · {sorted.length} rows
          </span>
          <button
            className="dt-action-btn"
            disabled={safePage >= pageCount - 1}
            onClick={() => onPageChange(Math.min(pageCount - 1, safePage + 1))}
          >
            Next →
          </button>
        </div>
      )}

      {refPreview && (
        <RefPreviewPopup
          targetPath={refPreview.target}
          refId={refPreview.id}
          onClose={() => setRefPreview(null)}
        />
      )}
    </>
  );
}
