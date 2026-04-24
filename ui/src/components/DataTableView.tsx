/**
 * DataTableView — renders a vault data-table file as an interactive CRUD table.
 *
 * Data lives in a vault .md file with `data-table-plugin: basic` frontmatter.
 * The backend parses Schema + Rows YAML blocks; this component reads/writes
 * through /vault/datatable endpoints. Row edits use FormRenderer (same as HITL
 * form dialogs) so the schema drives the form automatically.
 */

import { useCallback, useEffect, useState } from "react";
import FormRenderer from "./FormRenderer";
import Modal, { type ModalProps } from "./Modal";
import {
  addVaultDataTableRow,
  deleteVaultDataTableRow,
  getVaultDataTable,
  updateVaultDataTableRow,
  type DataTable,
} from "../api";
import type { FieldSchema } from "../types/form";
import "./DataTableView.css";

interface Props {
  path: string;
}

type RowRecord = Record<string, unknown>;

export default function DataTableView({ path }: Props) {
  const [table, setTable] = useState<DataTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingRow, setEditingRow] = useState<RowRecord | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);

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

  if (error) return <div className="dt-error">{error}</div>;
  if (!table) return <div className="dt-loading">Loading…</div>;

  const fields: FieldSchema[] = table.schema?.fields ?? [];
  const rows = table.rows ?? [];
  const fieldNames = fields.map((f) => f.name);

  async function handleAdd(values: Record<string, unknown>) {
    try {
      await addVaultDataTableRow(path, values);
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
      await updateVaultDataTableRow(path, rowId, values);
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

  const editingValues = editingRow
    ? Object.fromEntries(fieldNames.map((n) => [n, editingRow[n]]))
    : undefined;

  return (
    <div className="dt-container">
      {confirmModal && <Modal {...confirmModal} />}
      <div className="dt-header">
        <span className="dt-title">{table.schema?.title ?? "Data Table"}</span>
        <button
          className="vault-pill"
          onClick={() => {
            setShowAddForm(true);
            setEditingRow(null);
          }}
        >
          + Add Row
        </button>
      </div>

      {showAddForm && (
        <div className="dt-form-panel">
          <div className="dt-form-heading">New Row</div>
          <FormRenderer
            fields={fields}
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
            fields={fields}
            initialValues={editingValues}
            onSubmit={(v) => void handleUpdate(v)}
            onCancel={() => setEditingRow(null)}
            submitLabel="Save"
          />
        </div>
      )}

      {rows.length === 0 ? (
        <div className="dt-empty">No rows yet — click + Add Row to start.</div>
      ) : (
        <div className="dt-table-wrap">
          <table className="dt-table">
            <thead>
              <tr>
                {fieldNames.map((n) => (
                  <th key={n}>{fields.find((f) => f.name === n)?.label ?? n}</th>
                ))}
                <th className="dt-actions-col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const rowId = String(row._id ?? i);
                return (
                  <tr key={rowId}>
                    {fieldNames.map((n) => (
                      <td key={n}>{renderCell(row[n])}</td>
                    ))}
                    <td className="dt-actions-col">
                      <button
                        className="dt-action-btn"
                        onClick={() => {
                          setEditingRow(row);
                          setShowAddForm(false);
                        }}
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
    </div>
  );
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}
