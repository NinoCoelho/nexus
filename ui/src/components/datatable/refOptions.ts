// Shared helpers for kind: "ref" UI: target-path resolution and options loading.

import { useEffect, useState } from "react";
import type { FieldSchema } from "../../types/form";
import { getVaultDataTable } from "../../api/datatable";

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
 * Load picker options from the target table's primary-key + first text column.
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
        const tbl = await getVaultDataTable(target);
        if (cancelled) return;
        const tableMeta = (tbl.schema as { table?: { primary_key?: string } }).table ?? {};
        // Prefer the schema-declared primary_key. When absent, infer the
        // natural key: first `required: true` text/number field — that's
        // the user-facing identifier (e.g. "order_id" / "service_id").
        // Falling back to "_id" (the auto-assigned hash) makes the picker
        // offer values that don't match any existing ref data.
        const fields = tbl.schema.fields;
        let pk = tableMeta.primary_key;
        if (!pk) {
          const naturalPk = fields.find(
            (f) =>
              f.required === true &&
              (f.kind === "text" || f.kind === "number"),
          );
          pk = naturalPk?.name ?? "_id";
        }
        // Pick a labelField that's distinct from the pk and looks human-friendly.
        // Prefer a "name"/"title"/"label" field if any; otherwise the first
        // text field that isn't the pk.
        const NAME_HINTS = new Set(["name", "title", "label", "full_name"]);
        let labelField = fields.find(
          (f) => f.name !== pk && NAME_HINTS.has(f.name.toLowerCase()),
        );
        if (!labelField) {
          labelField = fields.find(
            (f) => f.name !== pk && (f.kind ?? "text") === "text",
          );
        }
        const opts = tbl.rows
          .map((r) => {
            const id = String(r[pk] ?? r._id ?? "");
            const labelVal = labelField ? String(r[labelField.name] ?? "").trim() : "";
            const label = labelVal ? `${id} — ${labelVal}` : id;
            return { id, label };
          })
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
