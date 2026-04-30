/**
 * Read-only popup shown when a user clicks a ref cell.
 *
 * Recursive: each `kind: ref` field inside the popup renders as a clickable
 * link (with the same "id — label" summary as the picker) that opens another
 * popup stacked on top. An inbound section below the field list shows rows
 * from other tables that reference this one (junction tables auto-collapse to
 * the far side, matching the existing related-rows backend); each list item
 * is itself clickable → another stacked popup. Drill is unbounded.
 *
 * Stack management:
 *   - Each level owns one optional `child` popup state. Closing a level
 *     unmounts everything beneath it.
 *   - Escape only closes the topmost popup, via a module-level depth counter.
 *   - Each popup renders into a portal anchored at <body> so its overlay
 *     doesn't sit inside its parent's overlay (prevents weird click bubbling
 *     and keeps z-index ordering simple).
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { FieldSchema } from "../../types/form";
import {
  deriveLabelInfo,
  fetchTableCached,
  resolveRefPath,
  summarizeRow,
} from "../datatable/refOptions";
import InlineRelatedSection from "./InlineRelatedSection";

export interface PopupCrumb {
  title: string;
  refId: string;
}

interface Props {
  targetPath: string;
  refId: string;
  onClose: () => void;
  onOpenTable?: (path: string, rowId: string) => void;
  /** Trail of ancestor popups for the breadcrumb. Top-level callers omit. */
  trail?: PopupCrumb[];
}

interface State {
  loading: boolean;
  error: string | null;
  row: Record<string, unknown> | null;
  fields: FieldSchema[];
  tableMeta: { primary_key?: string } | null;
  title: string;
  pkName: string;
  labelField: FieldSchema | null;
}

// Module-level depth counter so only the topmost popup reacts to Escape.
let _popupStack = 0;

export function RefPreviewPopup({
  targetPath,
  refId,
  onClose,
  onOpenTable,
  trail,
}: Props) {
  const [state, setState] = useState<State>({
    loading: true,
    error: null,
    row: null,
    fields: [],
    tableMeta: null,
    title: "",
    pkName: "_id",
    labelField: null,
  });
  const [child, setChild] = useState<{ targetPath: string; refId: string } | null>(null);
  const myDepthRef = useRef<number>(0);

  useEffect(() => {
    let cancelled = false;
    setChild(null);
    setState((s) => ({ ...s, loading: true, error: null, row: null }));
    (async () => {
      try {
        const tbl = await fetchTableCached(targetPath);
        if (cancelled) return;
        const tableMeta = (tbl.schema as { table?: { primary_key?: string } }).table ?? null;
        const { pkName, labelField } = deriveLabelInfo(tbl.schema.fields, tableMeta);
        const row =
          tbl.rows.find((r) => String(r[pkName] ?? "") === refId) ??
          tbl.rows.find((r) => String(r._id ?? "") === refId) ??
          null;
        setState({
          loading: false,
          error: null,
          row,
          fields: tbl.schema.fields,
          tableMeta,
          title: tbl.schema.title ?? targetPath.split("/").pop() ?? targetPath,
          pkName,
          labelField,
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

  // Escape: only the topmost popup closes. The depth counter tracks the
  // current nesting; when this instance's depth equals the live counter,
  // we're on top.
  useEffect(() => {
    myDepthRef.current = ++_popupStack;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && myDepthRef.current === _popupStack) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      _popupStack--;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const ownCrumb: PopupCrumb = {
    title: state.title || targetPath.split("/").pop() || targetPath,
    refId,
  };
  const crumbs = trail ?? [];

  const popup = (
    <div className="dt-ref-popup-overlay" onClick={onClose}>
      <div
        className="dt-ref-popup"
        role="dialog"
        aria-label={`Reference preview for ${refId}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dt-ref-popup-header">
          <div className="dt-ref-popup-titles">
            {crumbs.length > 0 && (
              <div className="dt-ref-popup-trail">
                {crumbs.map((c, i) => (
                  <span key={`${c.refId}-${i}`} className="dt-ref-popup-trail-crumb">
                    {c.title}: <strong>{c.refId}</strong>
                    <span className="dt-ref-popup-trail-sep"> · </span>
                  </span>
                ))}
              </div>
            )}
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
            <>
              <dl className="dt-ref-popup-fields">
                {state.fields.map((f) => {
                  const v = state.row?.[f.name];
                  if (v === null || v === undefined || v === "") return null;
                  return (
                    <div key={f.name} className="dt-ref-popup-row">
                      <dt>{f.label || f.name}</dt>
                      <dd>{renderFieldValue(v, f, targetPath, setChild)}</dd>
                    </div>
                  );
                })}
              </dl>

              <InlineRelatedSection
                hostPath={targetPath}
                rowId={String(state.row[state.pkName] ?? state.row._id ?? refId)}
                onPickRow={(picked) => setChild(picked)}
              />
            </>
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

  return (
    <>
      {createPortal(popup, document.body)}
      {child && (
        <RefPreviewPopup
          targetPath={child.targetPath}
          refId={child.refId}
          onClose={() => setChild(null)}
          onOpenTable={onOpenTable}
          trail={[...crumbs, ownCrumb]}
        />
      )}
    </>
  );
}

/**
 * Render a single field's value inside the popup body. For `kind: ref`
 * fields we replace the bare string with a `RefValueLink` (one per id when
 * cardinality is "many"); other kinds keep the plain-text fallback.
 */
function renderFieldValue(
  value: unknown,
  field: FieldSchema,
  hostPath: string,
  onPick: (next: { targetPath: string; refId: string }) => void,
): React.ReactNode {
  if (field.kind === "ref" && field.target_table) {
    const target = resolveRefPath(hostPath, field.target_table);
    if (!target) return Array.isArray(value) ? value.join(", ") : String(value);
    const ids = Array.isArray(value) ? value.map((v) => String(v)) : [String(value)];
    return (
      <span className="dt-ref-popup-value-refs">
        {ids.map((id, i) => (
          <span key={`${id}-${i}`}>
            {i > 0 && ", "}
            <RefValueLink targetPath={target} refId={id} onPick={onPick} />
          </span>
        ))}
      </span>
    );
  }
  return Array.isArray(value) ? value.join(", ") : String(value);
}

interface RefValueLinkProps {
  targetPath: string;
  refId: string;
  onPick: (next: { targetPath: string; refId: string }) => void;
}

/**
 * Clickable link rendering one referenced row's summary inside a popup body.
 * Lazy-loads the target table once (cached across the session) to derive the
 * "id — label" summary; while loading, falls back to showing the raw id so
 * the user can still click and drill.
 */
function RefValueLink({ targetPath, refId, onPick }: RefValueLinkProps) {
  const [label, setLabel] = useState<string>(refId);

  useEffect(() => {
    let cancelled = false;
    setLabel(refId);
    fetchTableCached(targetPath)
      .then((tbl) => {
        if (cancelled) return;
        const tableMeta = (tbl.schema as { table?: { primary_key?: string } }).table ?? null;
        const { pkName, labelField } = deriveLabelInfo(tbl.schema.fields, tableMeta);
        const row =
          tbl.rows.find((r) => String(r[pkName] ?? "") === refId) ??
          tbl.rows.find((r) => String(r._id ?? "") === refId) ??
          null;
        if (row) setLabel(summarizeRow(row, pkName, labelField));
      })
      .catch(() => {/* keep refId as fallback label */});
    return () => { cancelled = true; };
  }, [targetPath, refId]);

  return (
    <button
      type="button"
      className="dt-ref-popup-link"
      onClick={() => onPick({ targetPath, refId })}
      title={`Open ${targetPath}#${refId}`}
    >
      {label}
    </button>
  );
}
