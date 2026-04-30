/**
 * RelatedRowsPanel — shows rows from other tables that reference the
 * currently-edited row. Inbound one-to-many groups list referencing rows
 * directly; many-to-many junctions are collapsed (rows from the *other*
 * side of the junction are surfaced).
 *
 * Click a related row to navigate to its source table.
 */

import { useEffect, useState } from "react";
import {
  getRelatedRows,
  type RelatedRows,
  type OneToManyGroup,
  type ManyToManyGroup,
} from "../../api/datatable";

interface Props {
  path: string;
  rowId: string;
  /** Optional handler to open another table (e.g. via vault router). */
  onOpenTable?: (path: string) => void;
}

function summarize(row: Record<string, unknown>): string {
  // Prefer fields that look like a primary key or human title.
  const preferred = ["title", "name", "label", "id", "_id"];
  for (const key of preferred) {
    if (key in row && row[key] !== null && row[key] !== undefined && row[key] !== "") {
      return String(row[key]);
    }
  }
  const first = Object.entries(row).find(
    ([k, v]) => k !== "_id" && v !== null && v !== undefined && v !== "",
  );
  return first ? String(first[1]) : "(empty row)";
}

function RowLink({ row, onClick }: { row: Record<string, unknown>; onClick?: () => void }) {
  return (
    <li className="dt-related-row">
      <button
        className="dt-related-row-btn"
        onClick={onClick}
        disabled={!onClick}
        title={onClick ? "Open source table" : undefined}
      >
        {summarize(row)}
      </button>
    </li>
  );
}

export default function RelatedRowsPanel({ path, rowId, onOpenTable }: Props) {
  const [data, setData] = useState<RelatedRows | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    if (!rowId) return;
    getRelatedRows(path, rowId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError((e as Error).message ?? "load failed"); });
    return () => { cancelled = true; };
  }, [path, rowId]);

  if (error) {
    return <div className="dt-related-error">Could not load relations: {error}</div>;
  }
  if (!data) {
    return <div className="dt-related-loading">Loading relations…</div>;
  }

  const total = data.one_to_many.length + data.many_to_many.length;
  if (total === 0) {
    return null;
  }

  const renderOneToMany = (group: OneToManyGroup) => (
    <section key={`o2m-${group.from_table}-${group.field_name}`} className="dt-related-group">
      <div className="dt-related-group-head">
        <span className="dt-related-group-title">{group.from_title}</span>
        <span className="dt-related-group-meta">
          via {group.field_name} · {group.count} row{group.count === 1 ? "" : "s"}
        </span>
        <button
          className="dt-action-btn"
          onClick={() => onOpenTable?.(group.from_table)}
          disabled={!onOpenTable}
          title={`Open ${group.from_table}`}
        >
          Open table
        </button>
      </div>
      {group.rows.length === 0 ? (
        <div className="dt-related-empty">No related rows.</div>
      ) : (
        <ul className="dt-related-rows">
          {group.rows.map((r, i) => (
            <RowLink
              key={String(r._id ?? i)}
              row={r}
              onClick={onOpenTable ? () => onOpenTable(group.from_table) : undefined}
            />
          ))}
        </ul>
      )}
    </section>
  );

  const renderManyToMany = (group: ManyToManyGroup) => (
    <section key={`m2m-${group.junction_table}`} className="dt-related-group">
      <div className="dt-related-group-head">
        <span className="dt-related-group-title">{group.target_title}</span>
        <span className="dt-related-group-meta">
          via {group.junction_title} · {group.count} row{group.count === 1 ? "" : "s"}
        </span>
        <button
          className="dt-action-btn"
          onClick={() => onOpenTable?.(group.target_table)}
          disabled={!onOpenTable}
          title={`Open ${group.target_table}`}
        >
          Open table
        </button>
      </div>
      {group.rows.length === 0 ? (
        <div className="dt-related-empty">No related rows.</div>
      ) : (
        <ul className="dt-related-rows">
          {group.rows.map((r, i) => (
            <RowLink
              key={String(r._id ?? i)}
              row={r}
              onClick={onOpenTable ? () => onOpenTable(group.target_table) : undefined}
            />
          ))}
        </ul>
      )}
    </section>
  );

  return (
    <div className="dt-related-panel">
      <div className="dt-related-heading">Related</div>
      {data.one_to_many.map(renderOneToMany)}
      {data.many_to_many.map(renderManyToMany)}
    </div>
  );
}
