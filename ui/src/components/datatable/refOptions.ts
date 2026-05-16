// Shared helpers for kind: "ref" UI: target-path resolution, label inference,
// and options loading. The label-inference logic is also used by the recursive
// RefPreviewPopup drill-down so the same "id — name" summary appears in both
// the picker and the popup.

import { useEffect, useState } from "react";
import type { FieldSchema } from "../../types/form";
import { getVaultDataTable, type DataTable } from "../../api/datatable";

export interface RefOption {
  id: string;
  label: string;
}

/** Resolve a (possibly relative) target_table to a vault-absolute path. */
export function resolveRefPath(hostPath: string, target: string): string {
  if (!target) return "";
  if (!target.startsWith(".") && !target.startsWith("/")) return target;
  const hostParts = hostPath.split("/").slice(0, -1);
  const segments = target.replace(/^\/+/, "").split("/");
  for (const seg of segments) {
    if (seg === "..") hostParts.pop();
    else if (seg !== ".") hostParts.push(seg);
  }
  return hostParts.join("/");
}

/**
 * Pick the row's identifier column and a friendly label column.
 *
 * - Primary key: the schema's explicit ``table.primary_key`` if present;
 *   otherwise the first ``required: true`` text/number field (the natural
 *   key); otherwise ``_id`` (the auto-assigned hash).
 * - Label field: the first column whose name hints at being a display name
 *   (``name``/``title``/``label``/``full_name``); otherwise the first text
 *   field that isn't the primary key. Returns ``null`` if no good label
 *   column exists — caller falls back to id-only summaries.
 */
export function deriveLabelInfo(
  fields: FieldSchema[],
  tableMeta: { primary_key?: string } | null | undefined,
): { pkName: string; labelField: FieldSchema | null } {
  let pk = tableMeta?.primary_key;
  if (!pk) {
    pk = "_id";
  }
  const NAME_HINTS = new Set(["name", "title", "label", "full_name"]);
  let labelField = fields.find(
    (f) => f.name !== pk && NAME_HINTS.has(f.name.toLowerCase()),
  );
  if (!labelField) {
    labelField = fields.find(
      (f) => f.name !== pk && (f.kind ?? "text") === "text",
    );
  }
  return { pkName: pk, labelField: labelField ?? null };
}

/**
 * Format a row as ``"<id> — <label-value>"``, or just ``"<id>"`` when the
 * label is missing or empty. Mirrors the picker's existing format so all
 * ref UI shows the same summaries.
 */
export function summarizeRow(
  row: Record<string, unknown>,
  pkName: string,
  labelField: FieldSchema | null,
): string {
  const id = String(row[pkName] ?? row._id ?? "");
  if (!labelField) return id;
  const labelVal = String(row[labelField.name] ?? "").trim();
  return labelVal ? `${id} — ${labelVal}` : id;
}

/**
 * Suggest the next primary-key value based on existing rows.
 *
 * Detects the dominant ``<prefix><digits>`` pattern across existing values
 * (e.g. rows ``C001 C002 C003`` → suggest ``C004``; rows ``ORD-0001 ORD-0002``
 * → ``ORD-0003`` with the same zero-pad width). When values don't follow a
 * numeric-suffix pattern, returns ``undefined`` so the form leaves the field
 * empty for the user to fill in.
 *
 * Mixed-prefix tables fall back to the most common prefix; outlier values are
 * ignored. Empty tables return ``undefined`` (no precedent to extrapolate).
 */
export function suggestNextPk(
  rows: Record<string, unknown>[],
  pkName: string,
): string | undefined {
  const values = rows
    .map((r) => r[pkName])
    .filter((v): v is string | number => v !== undefined && v !== null && v !== "")
    .map(String);
  if (values.length === 0) return undefined;

  // Group by prefix (everything before the trailing digit run).
  const groups = new Map<string, { width: number; max: number; count: number }>();
  for (const v of values) {
    const m = v.match(/^(.*?)(\d+)$/);
    if (!m) continue;
    const prefix = m[1];
    const num = parseInt(m[2], 10);
    const width = m[2].length;
    const existing = groups.get(prefix);
    if (!existing) {
      groups.set(prefix, { width, max: num, count: 1 });
    } else {
      existing.max = Math.max(existing.max, num);
      existing.width = Math.max(existing.width, width);
      existing.count += 1;
    }
  }
  if (groups.size === 0) return undefined;

  // Pick the most-common prefix; ties resolve to the prefix with the higher
  // max so we always advance the dominant numbering scheme.
  let best: { prefix: string; width: number; max: number; count: number } | null = null;
  for (const [prefix, info] of groups) {
    if (
      !best ||
      info.count > best.count ||
      (info.count === best.count && info.max > best.max)
    ) {
      best = { prefix, ...info };
    }
  }
  if (!best) return undefined;

  const nextNum = best.max + 1;
  const padded = String(nextNum).padStart(best.width, "0");
  return `${best.prefix}${padded}`;
}


/**
 * Module-level cache of in-flight + completed ``getVaultDataTable`` promises,
 * keyed by absolute vault path. The recursive ref-drill popup may resolve the
 * same target table multiple times within a single session (sibling fields
 * on the same row, or repeated drill-downs into the same entity). Sharing a
 * promise per path means the second resolver awaits the same fetch instead
 * of re-hitting the server.
 */
const _tableCache = new Map<string, Promise<DataTable>>();

export function fetchTableCached(absPath: string): Promise<DataTable> {
  let p = _tableCache.get(absPath);
  if (!p) {
    p = getVaultDataTable(absPath).catch((err) => {
      // Drop failed entries so the next call retries instead of returning
      // the stale rejection forever.
      _tableCache.delete(absPath);
      throw err;
    });
    _tableCache.set(absPath, p);
  }
  return p;
}

/**
 * Load picker options from the target table's primary-key + label column.
 * Returns `null` while loading; `[]` when target is unset or load fails.
 */
export function useRefOptions(field: FieldSchema, hostPath: string): {
  options: RefOption[] | null;
  error: string | null;
} {
  const target = resolveRefPath(hostPath, field.target_table ?? "");
  const [state, setState] = useState<{ options: RefOption[] | null; error: string | null }>({
    options: null,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    if (!target) {
      setState({ options: [], error: null });
      return;
    }
    setState({ options: null, error: null });
    (async () => {
      try {
        const tbl = await fetchTableCached(target);
        if (cancelled) return;
        const tableMeta = (tbl.schema as { table?: { primary_key?: string } }).table ?? null;
        const { pkName, labelField } = deriveLabelInfo(tbl.schema.fields, tableMeta);
        const opts = tbl.rows
          .map((r) => ({ id: String(r[pkName] ?? r._id ?? ""), label: summarizeRow(r, pkName, labelField) }))
          .filter((o) => o.id);
        setState({ options: opts, error: null });
      } catch (e) {
        if (cancelled) return;
        setState({ options: [], error: (e as Error).message ?? "load failed" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [target]);

  return state;
}
