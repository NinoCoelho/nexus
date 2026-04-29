/**
 * Lightweight popup shown when a user clicks a ref cell.
 *
 * Fetches the referenced row from the target data-table and renders its
 * fields. Supports navigating to the full table view via "Open table".
 */

import { useEffect, useState } from "react";
import { getVaultDataTable } from "../../api/datatable";
import type { FieldSchema } from "../../types/form";

interface Props {
  targetPath: string;
  refId: string;
  onClose: () => void;
  onOpenTable?: (path: string, rowId: string) => void;
}

interface State {
  loading: boolean;
  error: string | null;
  row: Record<string, unknown> | null;
  fields: FieldSchema[];
  title: string;
  pkName: string;
}

function inferPk(fields: FieldSchema[], tableMeta: { primary_key?: string } | null | undefined): string {
  const explicit = tableMeta?.primary_key;
  if (explicit) return explicit;
  const natural = fields.find(
    (f) => f.required === true && (f.kind === "text" || f.kind === "number"),
  );
  return natural?.name ?? "_id";
}

export function RefPreviewPopup({ targetPath, refId, onClose, onOpenTable }: Props) {
  const [state, setState] = useState<State>({
    loading: true,
    error: null,
    row: null,
    fields: [],
    title: "",
    pkName: "_id",
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const tbl = await getVaultDataTable(targetPath);
        if (cancelled) return;
        const tableMeta = (tbl.schema as { table?: { primary_key?: string } }).table ?? null;
        const pk = inferPk(tbl.schema.fields, tableMeta);
        const row =
          tbl.rows.find((r) => String(r[pk] ?? "") === refId) ??
          tbl.rows.find((r) => String(r._id ?? "") === refId) ??
          null;
        setState({
          loading: false,
          error: null,
          row,
          fields: tbl.schema.fields,
          title: tbl.schema.title ?? targetPath.split("/").pop() ?? targetPath,
          pkName: pk,
        });
      } catch (err) {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [targetPath, refId]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="dt-ref-popup-overlay" onClick={onClose}>
      <div
        className="dt-ref-popup"
        role="dialog"
        aria-label={`Reference preview for ${refId}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dt-ref-popup-header">
          <div className="dt-ref-popup-titles">
            <div className="dt-ref-popup-title">{state.title}</div>
            <div className="dt-ref-popup-subtitle">{refId}</div>
          </div>
          <button
            type="button"
            className="dt-ref-popup-close"
            onClick={onClose}
            aria-label="Close preview"
          >
            ×
          </button>
        </div>

        <div className="dt-ref-popup-body">
          {state.loading && <div className="dt-ref-popup-status">Loading…</div>}
          {state.error && (
            <div className="dt-ref-popup-status dt-ref-popup-status--error">
              {state.error}
            </div>
          )}
          {!state.loading && !state.error && !state.row && (
            <div className="dt-ref-popup-status">
              Could not find a row with id <code>{refId}</code> in this table.
            </div>
          )}
          {!state.loading && !state.error && state.row && (
            <dl className="dt-ref-popup-fields">
              {state.fields.map((f) => {
                const v = state.row?.[f.name];
                if (v === null || v === undefined || v === "") return null;
                return (
                  <div key={f.name} className="dt-ref-popup-row">
                    <dt>{f.label || f.name}</dt>
                    <dd>{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
                  </div>
                );
              })}
            </dl>
          )}
        </div>

        <div className="dt-ref-popup-footer">
          {onOpenTable && state.row && (
            <button
              type="button"
              className="dt-ref-popup-action"
              onClick={() => onOpenTable(targetPath, refId)}
            >
              Open table
            </button>
          )}
          <button type="button" className="dt-ref-popup-action" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
