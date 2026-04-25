// DataTableView — the table grid with sortable headers, inline editing, row actions, and pagination.

import type { FieldSchema } from "../../types/form";
import InlineEditor from "./InlineEditor";
import { renderCell } from "./utils";

type RowRecord = Record<string, unknown>;

const INLINE_EDITABLE: ReadonlySet<string> = new Set([
  "text", "number", "date", "select", "boolean", "vault-link",
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
  onToggleSort: (name: string) => void;
  onStartEdit: (rowId: string, field: FieldSchema, value: unknown) => void;
  onCellDraftChange: (v: unknown) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onEditRow: (row: RowRecord) => void;
  onDeleteRow: (rowId: string) => void;
  onPageChange: (next: number) => void;
}

export default function DataTableGrid({
  visibleFields, pageRows, sorted, rows, fields, sort,
  editingCell, cellDraft, safePage, pageCount,
  onToggleSort, onStartEdit, onCellDraftChange, onCommitEdit, onCancelEdit,
  onEditRow, onDeleteRow, onPageChange,
}: Props) {
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
                            onChange={onCellDraftChange}
                            onCommit={() => void onCommitEdit()}
                            onCancel={onCancelEdit}
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
    </>
  );
}
