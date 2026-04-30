/**
 * InlineRelatedSection — compact "Related" panel for a single row, shown
 * inside RefPreviewPopup. Each inbound 1:N or M:N (junction-collapsed) group
 * lists rows as clickable buttons; clicking pushes another popup on the stack.
 *
 * Sibling of RelatedRowsPanel (used in the row-edit form). The two have
 * different ergonomics: the panel exposes "Open table" + "Open in chat"
 * actions for editing flows, while this section is purely for drill-down
 * inside the read-only popup. Sharing data-loading via getRelatedRows; the
 * UI is intentionally distinct.
 */

import { useEffect, useMemo, useState } from "react";
import {
  getRelatedRows,
  type RelatedRows,
  type OneToManyGroup,
  type ManyToManyGroup,
} from "../../api/datatable";
import {
  deriveLabelInfo,
  fetchTableCached,
  summarizeRow,
} from "../datatable/refOptions";
import type { FieldSchema } from "../../types/form";

const PREVIEW_LIMIT = 5;

interface Props {
  hostPath: string;
  rowId: string;
  onPickRow: (next: { targetPath: string; refId: string }) => void;
}

interface SchemaInfo {
  pkName: string;
  labelField: FieldSchema | null;
}

export default function InlineRelatedSection({ hostPath, rowId, onPickRow }: Props) {
  const [data, setData] = useState<RelatedRows | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Per-target-path schema info, populated on demand so each group can format
  // its rows. Keys are absolute vault paths.
  const [schemaInfo, setSchemaInfo] = useState<Record<string, SchemaInfo>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setSchemaInfo({});
    setExpanded(new Set());
    if (!rowId) return;
    getRelatedRows(hostPath, rowId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError((e as Error).message ?? "load failed"); });
    return () => { cancelled = true; };
  }, [hostPath, rowId]);

  // Whenever the data arrives, kick off schema fetches for every distinct
  // target table referenced by the groups so we can render row summaries.
  useEffect(() => {
    if (!data) return;
    const targets = new Set<string>();
    for (const g of data.one_to_many) targets.add(g.from_table);
    for (const g of data.many_to_many) targets.add(g.target_table);
    let cancelled = false;
    targets.forEach((t) => {
      if (schemaInfo[t]) return;
      fetchTableCached(t)
        .then((tbl) => {
          if (cancelled) return;
          const meta = (tbl.schema as { table?: { primary_key?: string } }).table ?? null;
          const info = deriveLabelInfo(tbl.schema.fields, meta);
          setSchemaInfo((prev) => ({ ...prev, [t]: info }));
        })
        .catch(() => {/* fall back to id-only summaries */});
    });
    return () => { cancelled = true; };
  }, [data, schemaInfo]);

  const totalGroups = useMemo(
    () => (data ? data.one_to_many.length + data.many_to_many.length : 0),
    [data],
  );

  if (error) {
    return <div className="dt-ref-popup-related-error">Could not load related rows: {error}</div>;
  }
  if (!data) {
    return <div className="dt-ref-popup-related-loading">Loading related…</div>;
  }
  if (totalGroups === 0) return null;

  const renderGroup = (
    key: string,
    targetPath: string,
    title: string,
    via: string,
    rows: Record<string, unknown>[],
  ) => {
    const info = schemaInfo[targetPath];
    const isExpanded = expanded.has(key);
    const visible = isExpanded ? rows : rows.slice(0, PREVIEW_LIMIT);
    const hidden = rows.length - visible.length;
    return (
      <section key={key} className="dt-ref-popup-related-group">
        <div className="dt-ref-popup-related-group-head">
          <span className="dt-ref-popup-related-group-title">{title}</span>
          <span className="dt-ref-popup-related-group-meta">{via} · {rows.length}</span>
        </div>
        <ul className="dt-ref-popup-related-list">
          {visible.map((r, i) => {
            const summary = info
              ? summarizeRow(r, info.pkName, info.labelField)
              : String(r._id ?? "(row)");
            const id = info
              ? String(r[info.pkName] ?? r._id ?? "")
              : String(r._id ?? "");
            return (
              <li key={String(r._id ?? i)} className="dt-ref-popup-related-row">
                <button
                  type="button"
                  className="dt-ref-popup-related-row-btn"
                  onClick={() => onPickRow({ targetPath, refId: id })}
                  disabled={!id}
                  title={id ? `Open ${targetPath}#${id}` : "(missing id)"}
                >
                  {summary}
                </button>
              </li>
            );
          })}
        </ul>
        {hidden > 0 && (
          <button
            type="button"
            className="dt-ref-popup-related-show-all"
            onClick={() =>
              setExpanded((prev) => {
                const next = new Set(prev);
                next.add(key);
                return next;
              })
            }
          >
            Show all ({rows.length})
          </button>
        )}
      </section>
    );
  };

  return (
    <div className="dt-ref-popup-related">
      <div className="dt-ref-popup-related-heading">Related</div>
      {data.one_to_many.map((g: OneToManyGroup) =>
        renderGroup(
          `o2m-${g.from_table}-${g.field_name}`,
          g.from_table,
          g.from_title,
          `via ${g.field_name}`,
          g.rows,
        ),
      )}
      {data.many_to_many.map((g: ManyToManyGroup) =>
        renderGroup(
          `m2m-${g.junction_table}-${g.target_table}`,
          g.target_table,
          g.target_title,
          `via ${g.junction_title}`,
          g.rows,
        ),
      )}
    </div>
  );
}
