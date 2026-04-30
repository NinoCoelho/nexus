/**
 * RelatedRowsPanel — rich CRUD-capable inbound-rows panel for the row-edit form.
 *
 * Each inbound 1:N or junction-collapsed M:N group renders as a mini grid:
 *   - up to MAX_COLS columns picked from the inbound table's schema, skipping
 *     the FK column (it's redundant — we know it points at us), formula
 *     fields (computed), and textareas (too tall for the mini grid);
 *   - pagination at PAGE_SIZE per page;
 *   - 1:N groups gain an "+ Add" affordance — the form is pre-filled with
 *     the parent's PK as the FK and that field is hidden from the form,
 *     since the user is editing FROM the parent and shouldn't have to
 *     re-enter that link;
 *   - per-row Edit + Del buttons.
 *
 * M:N groups (junction-collapsed) render the same table read-only — adding
 * a row would mean creating a junction entry, which isn't a single-form
 * gesture; "Open table" navigates to the junction for full CRUD.
 *
 * Drill-down navigation (clicking a row to open another popup) lives in the
 * sibling InlineRelatedSection, which is mounted inside RefPreviewPopup.
 * This panel is the editing surface; that one is the read-only viewer.
 */

import { useCallback, useEffect, useState } from "react";
import {
  addVaultDataTableRow,
  deleteVaultDataTableRow,
  getRelatedRows,
  updateVaultDataTableRow,
  type ManyToManyGroup,
  type OneToManyGroup,
  type RelatedRows,
} from "../../api/datatable";
import {
  deriveLabelInfo,
  fetchTableCached,
  suggestNextPk,
} from "../datatable/refOptions";
import type { FieldSchema } from "../../types/form";
import FormRenderer from "../FormRenderer";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";

const PAGE_SIZE = 5;
const SKIP_KINDS = new Set(["textarea", "formula"]);
const MAX_COLS = 4;

interface Props {
  /** Parent table path (e.g. ``data/petshop/customers.md``). */
  path: string;
  /** Parent row's PK value (e.g. ``"C002"``). Inbound rows are keyed against this. */
  rowId: string;
  /** Optional handler to open another table fully (e.g. via vault router). */
  onOpenTable?: (path: string) => void;
}

interface GroupSchema {
  fields: FieldSchema[];
  pkName: string;
  /** Full row set of the inbound table — used to suggest the next PK on Add. */
  allRows: Record<string, unknown>[];
}

function UnmatchedHint({
  fieldName,
  sample,
  rowId,
}: {
  fieldName: string;
  sample?: string[];
  rowId: string;
}) {
  if (!sample || sample.length === 0) {
    return <div className="dt-related-empty">No related rows.</div>;
  }
  return (
    <div className="dt-related-empty dt-related-unmatched">
      No rows reference <code>{rowId}</code>. The <code>{fieldName}</code>{" "}
      column does store{" "}
      {sample.map((s, i) => (
        <span key={s}>
          {i > 0 && ", "}
          <code>{s}</code>
        </span>
      ))}
      {sample.length === 3 && "…"}.
    </div>
  );
}

export default function RelatedRowsPanel({ path, rowId, onOpenTable }: Props) {
  const toast = useToast();
  const [data, setData] = useState<RelatedRows | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [groupSchemas, setGroupSchemas] = useState<Record<string, GroupSchema>>({});
  const [activeForm, setActiveForm] = useState<{
    groupTable: string;
    fkField: string | null;
    edit?: { _id: string; values: Record<string, unknown> };
  } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    groupTable: string;
    rowId: string;
  } | null>(null);
  const [pages, setPages] = useState<Record<string, number>>({});

  const reload = useCallback(async () => {
    if (!rowId) return;
    setError(null);
    try {
      const next = await getRelatedRows(path, rowId);
      setData(next);
    } catch (e) {
      setError((e as Error).message ?? "load failed");
    }
  }, [path, rowId]);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setGroupSchemas({});
    setPages({});
    if (!rowId) return;
    getRelatedRows(path, rowId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError((e as Error).message ?? "load failed"); });
    return () => { cancelled = true; };
  }, [path, rowId]);

  // Fetch each group's schema once so we can render the right columns + form.
  useEffect(() => {
    if (!data) return;
    const targets = new Set<string>();
    for (const g of data.one_to_many) targets.add(g.from_table);
    for (const g of data.many_to_many) targets.add(g.target_table);
    let cancelled = false;
    targets.forEach((t) => {
      if (groupSchemas[t]) return;
      fetchTableCached(t)
        .then((tbl) => {
          if (cancelled) return;
          const meta = (tbl.schema as { table?: { primary_key?: string } }).table ?? null;
          const { pkName } = deriveLabelInfo(tbl.schema.fields, meta);
          setGroupSchemas((prev) => ({
            ...prev,
            [t]: { fields: tbl.schema.fields, pkName, allRows: tbl.rows },
          }));
        })
        .catch(() => {/* fall back to id-only summaries */});
    });
    return () => { cancelled = true; };
  }, [data, groupSchemas]);

  if (error) {
    return <div className="dt-related-error">Could not load relations: {error}</div>;
  }
  if (!data) {
    return <div className="dt-related-loading">Loading relations…</div>;
  }

  const total = data.one_to_many.length + data.many_to_many.length;
  if (total === 0) return null;

  function visibleColumns(groupSchema: GroupSchema, fkField: string | null): FieldSchema[] {
    return groupSchema.fields
      .filter((f) =>
        f.name !== fkField && !SKIP_KINDS.has(f.kind ?? "text"),
      )
      .slice(0, MAX_COLS);
  }

  function formFields(groupSchema: GroupSchema, fkField: string | null): FieldSchema[] {
    return groupSchema.fields.filter(
      (f) => f.name !== fkField && f.kind !== "formula" && f.name !== "_id",
    );
  }

  function renderCellValue(value: unknown, field: FieldSchema): string {
    if (value === null || value === undefined || value === "") return "";
    if (field.kind === "boolean") return value ? "✓" : "";
    if (Array.isArray(value)) return value.join(", ");
    return String(value);
  }

  const handleAddSubmit = async (
    groupTable: string,
    fkField: string,
    values: Record<string, unknown>,
  ) => {
    try {
      await addVaultDataTableRow(groupTable, { ...values, [fkField]: rowId });
      toast.success("Added");
      setActiveForm(null);
      void reload();
    } catch (e) {
      toast.error("Couldn't add row", { detail: (e as Error).message });
    }
  };

  const handleEditSubmit = async (
    groupTable: string,
    fkField: string | null,
    editId: string,
    values: Record<string, unknown>,
  ) => {
    try {
      // FK isn't in the form; preserve it on PATCH so the link doesn't break.
      const payload = fkField ? { ...values, [fkField]: rowId } : values;
      await updateVaultDataTableRow(groupTable, editId, payload);
      toast.success("Saved");
      setActiveForm(null);
      void reload();
    } catch (e) {
      toast.error("Couldn't save", { detail: (e as Error).message });
    }
  };

  const handleDelete = async (groupTable: string, rowToDelete: string) => {
    try {
      await deleteVaultDataTableRow(groupTable, rowToDelete);
      toast.success("Deleted");
      setPendingDelete(null);
      void reload();
    } catch (e) {
      toast.error("Couldn't delete", { detail: (e as Error).message });
      setPendingDelete(null);
    }
  };

  function renderGroup({
    key,
    groupTable,
    title,
    via,
    rows,
    count,
    fkField,
    canMutate,
    unmatched,
    emptyHintField,
  }: {
    key: string;
    groupTable: string;
    title: string;
    via: string;
    rows: Record<string, unknown>[];
    count: number;
    fkField: string | null;
    canMutate: boolean;
    unmatched?: string[];
    emptyHintField: string;
  }) {
    const schema = groupSchemas[groupTable];
    const visibleCols = schema ? visibleColumns(schema, fkField) : [];
    const page = pages[key] ?? 0;
    const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const safePage = Math.min(page, pageCount - 1);
    const pageRows = rows.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

    return (
      <section key={key} className="dt-related-group">
        <header className="dt-related-group-head">
          <div className="dt-related-group-titles">
            <span className="dt-related-group-title">{title}</span>
            <span className="dt-related-group-meta">
              {via} · {count} row{count === 1 ? "" : "s"}
            </span>
          </div>
          <div className="dt-related-group-actions">
            {canMutate && fkField && schema && (
              <button
                type="button"
                className="dt-action-btn"
                onClick={() => setActiveForm({ groupTable, fkField })}
                title={`Add a new row to ${groupTable}`}
              >
                + Add
              </button>
            )}
            <button
              type="button"
              className="dt-action-btn"
              onClick={() => onOpenTable?.(groupTable)}
              disabled={!onOpenTable}
              title={`Open ${groupTable}`}
            >
              Open table
            </button>
          </div>
        </header>

        {count === 0 ? (
          <UnmatchedHint
            fieldName={emptyHintField}
            sample={unmatched}
            rowId={rowId}
          />
        ) : !schema ? (
          <div className="dt-related-loading">loading schema…</div>
        ) : (
          <>
            <table className="dt-related-mini">
              <thead>
                <tr>
                  {visibleCols.map((c) => (
                    <th key={c.name}>{c.label ?? c.name}</th>
                  ))}
                  {canMutate && <th className="dt-related-mini-actions" />}
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r) => {
                  const editId = String(r._id ?? "");
                  return (
                    <tr key={editId || JSON.stringify(r)}>
                      {visibleCols.map((c) => (
                        <td key={c.name}>{renderCellValue(r[c.name], c)}</td>
                      ))}
                      {canMutate && (
                        <td className="dt-related-mini-actions">
                          <button
                            type="button"
                            className="dt-action-btn"
                            disabled={!editId}
                            onClick={() =>
                              setActiveForm({
                                groupTable,
                                fkField,
                                edit: { _id: editId, values: r },
                              })
                            }
                            title="Edit row"
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="dt-action-btn dt-action-btn--delete"
                            disabled={!editId}
                            onClick={() => setPendingDelete({ groupTable, rowId: editId })}
                            title="Delete row"
                          >
                            Del
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length > PAGE_SIZE && (
              <div className="dt-related-pagination">
                <button
                  type="button"
                  className="dt-action-btn"
                  disabled={safePage === 0}
                  onClick={() =>
                    setPages((prev) => ({ ...prev, [key]: Math.max(0, safePage - 1) }))
                  }
                >
                  ← Prev
                </button>
                <span className="dt-related-pagination-info">
                  Page {safePage + 1} of {pageCount}
                </span>
                <button
                  type="button"
                  className="dt-action-btn"
                  disabled={safePage >= pageCount - 1}
                  onClick={() =>
                    setPages((prev) => ({
                      ...prev,
                      [key]: Math.min(pageCount - 1, safePage + 1),
                    }))
                  }
                >
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </section>
    );
  }

  return (
    <div className="dt-related-panel">
      <div className="dt-related-heading">Related</div>

      {data.one_to_many.map((g: OneToManyGroup) =>
        renderGroup({
          key: `o2m-${g.from_table}-${g.field_name}`,
          groupTable: g.from_table,
          title: g.from_title,
          via: `via ${g.field_name}`,
          rows: g.rows,
          count: g.count,
          fkField: g.field_name,
          canMutate: true,
          unmatched: g.unmatched_sample,
          emptyHintField: g.field_name,
        }),
      )}

      {data.many_to_many.map((g: ManyToManyGroup) =>
        renderGroup({
          key: `m2m-${g.junction_table}-${g.target_table}`,
          groupTable: g.target_table,
          title: g.target_title,
          via: `via ${g.junction_title}`,
          rows: g.rows,
          count: g.count,
          fkField: null,
          canMutate: false,
          unmatched: g.unmatched_sample,
          emptyHintField: g.junction_title,
        }),
      )}

      {activeForm && groupSchemas[activeForm.groupTable] && (() => {
        const gs = groupSchemas[activeForm.groupTable];
        let initialValues = activeForm.edit?.values;
        if (!activeForm.edit) {
          // On Add, suggest the next primary-key value based on existing rows
          // in the inbound table. The FK is injected at submit, so it doesn't
          // need to appear here.
          const suggested = suggestNextPk(gs.allRows, gs.pkName);
          if (suggested) initialValues = { [gs.pkName]: suggested };
        }
        return (
          <div className="dt-modal-overlay" onClick={() => setActiveForm(null)}>
            <div
              className="dt-modal"
              onClick={(e) => e.stopPropagation()}
              style={{ minWidth: 420 }}
            >
              <div className="dt-modal-title">
                {activeForm.edit ? "Edit row" : "Add row"}
              </div>
              <FormRenderer
                hostPath={activeForm.groupTable}
                fields={formFields(gs, activeForm.fkField)}
                initialValues={initialValues}
                submitLabel={activeForm.edit ? "Save" : "Add"}
                onCancel={() => setActiveForm(null)}
                onSubmit={(values) => {
                  if (activeForm.edit) {
                    void handleEditSubmit(
                      activeForm.groupTable,
                      activeForm.fkField,
                      activeForm.edit._id,
                      values,
                    );
                  } else if (activeForm.fkField) {
                    void handleAddSubmit(
                      activeForm.groupTable,
                      activeForm.fkField,
                      values,
                    );
                  }
                }}
              />
            </div>
          </div>
        );
      })()}

      {pendingDelete && (
        <Modal
          kind="confirm"
          title="Delete this row?"
          message="This cannot be undone."
          confirmLabel="Delete"
          danger
          onSubmit={() =>
            void handleDelete(pendingDelete.groupTable, pendingDelete.rowId)
          }
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
