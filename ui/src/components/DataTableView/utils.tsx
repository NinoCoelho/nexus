// DataTableView — pure helpers: cell rendering, sorting, CSV coercion, formula stripping.

import React from "react";
import type { FieldSchema } from "../../types/form";

export function renderCell(value: unknown, field: FieldSchema): React.ReactNode {
  if (value === null || value === undefined || value === "") return "";
  const kind = field.kind ?? "text";
  if (kind === "boolean") return value ? "✓" : "";
  if (kind === "vault-link") {
    const v = String(value);
    return <a href={`vault://${v}`}>{v}</a>;
  }
  if (kind === "formula") {
    return <span className="dt-cell-formula">{String(value)}</span>;
  }
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

export function cmp(a: unknown, b: unknown): number {
  const an = a === null || a === undefined || a === "";
  const bn = b === null || b === undefined || b === "";
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  // try numeric compare on strings that look like numbers
  const ax = typeof a === "string" ? parseFloat(a) : NaN;
  const bx = typeof b === "string" ? parseFloat(b) : NaN;
  if (!Number.isNaN(ax) && !Number.isNaN(bx) && /^-?\d/.test(String(a)) && /^-?\d/.test(String(b))) {
    return ax - bx;
  }
  return String(a).localeCompare(String(b));
}

export function stripFormulas(values: Record<string, unknown>, fields: FieldSchema[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const formulaNames = new Set(fields.filter((f) => f.kind === "formula").map((f) => f.name));
  for (const [k, v] of Object.entries(values)) {
    if (!formulaNames.has(k)) out[k] = v;
  }
  return out;
}

export function coerceCSVValue(raw: string | undefined, field: FieldSchema | undefined): unknown {
  if (raw === undefined) return "";
  if (!field) return raw;
  const kind = field.kind ?? "text";
  if (kind === "number") {
    const n = parseFloat(raw);
    return Number.isNaN(n) ? "" : n;
  }
  if (kind === "boolean") {
    const v = raw.trim().toLowerCase();
    return v === "true" || v === "1" || v === "yes" || v === "✓";
  }
  if (kind === "multiselect") {
    return raw.split(/[;,]/).map((s) => s.trim()).filter(Boolean);
  }
  return raw;
}
