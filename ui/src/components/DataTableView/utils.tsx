// DataTableView — pure helpers: cell rendering, sorting, CSV coercion, formula stripping.

import React from "react";
import type { FieldSchema } from "../../types/form";

export interface RenderCellOptions {
  /** When provided, ref cells render as buttons that invoke this with
   *  the absolute target path + the ref id, so the parent can show a popup. */
  onRefClick?: (target: string, id: string) => void;
  /** Used to resolve relative target_table paths. */
  hostPath?: string;
}

function resolveTargetPath(hostPath: string | undefined, target: string): string {
  if (!target) return "";
  if (!target.startsWith(".") && !target.startsWith("/")) return target;
  const hostParts = (hostPath ?? "").split("/").slice(0, -1);
  for (const seg of target.replace(/^\/+/, "").split("/")) {
    if (seg === "..") hostParts.pop();
    else if (seg !== ".") hostParts.push(seg);
  }
  return hostParts.join("/");
}

export function renderCell(
  value: unknown,
  field: FieldSchema,
  opts: RenderCellOptions = {},
): React.ReactNode {
  if (value === null || value === undefined || value === "") return "";
  const kind = field.kind ?? "text";
  if (kind === "boolean") return value ? "✓" : "";
  if (kind === "vault-link") {
    const v = String(value);
    return <a href={`vault://${v}`}>{v}</a>;
  }
  if (kind === "ref") {
    const target = resolveTargetPath(opts.hostPath, field.target_table ?? "");
    const onRefClick = opts.onRefClick;
    const renderOne = (v: unknown) => {
      const s = String(v);
      if (onRefClick && target) {
        return (
          <button
            key={s}
            type="button"
            className="dt-cell-ref-btn"
            onClick={(e) => {
              e.stopPropagation();
              onRefClick(target, s);
            }}
            title={`Open ${target}#${s}`}
          >
            {s}
          </button>
        );
      }
      const href = target ? `vault://${target}#${encodeURIComponent(s)}` : `vault://${s}`;
      return <a key={s} href={href}>{s}</a>;
    };
    if (Array.isArray(value)) {
      return (
        <span className="dt-cell-ref-multi">
          {value.map((v, i) => (
            <span key={i}>{i > 0 && ", "}{renderOne(v)}</span>
          ))}
        </span>
      );
    }
    return renderOne(value);
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
