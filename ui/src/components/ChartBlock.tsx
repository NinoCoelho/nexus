/**
 * ChartBlock — inline chart renderer for ```nexus-chart``` fenced blocks.
 *
 * Parses the nexus-chart YAML spec and transforms it into a mermaid
 * xychart-beta / pie source, then delegates rendering to MermaidBlock.
 * Reuses the already-loaded mermaid runtime (no new dependency).
 *
 * Chart spec (YAML inside the fence):
 *   type: bar | line | pie
 *   title: optional string
 *   x: field name in data (used as label)
 *   y: field name in data (used as value)
 *   data:
 *     - { x: "A", y: 3 }
 *     - { x: "B", y: 7 }
 */

import { useMemo } from "react";
import { MermaidBlock } from "./MarkdownView";

interface DataPoint {
  x: string;
  y: number;
}

interface ChartSpec {
  type?: "bar" | "line" | "pie";
  title?: string;
  x?: string;
  y?: string;
  data?: unknown[];
}

// ── YAML parser (tiny, no dep) ──────────────────────────────────────────────

function parseScalar(raw: string): unknown {
  const v = raw.trim();
  if (v === "") return "";
  if (v === "true") return true;
  if (v === "false") return false;
  if (v === "null" || v === "~") return null;
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1);
  }
  const num = Number(v);
  return Number.isFinite(num) && /^-?\d/.test(v) ? num : v;
}

function parseInlineObject(src: string): Record<string, unknown> | null {
  const inner = src.trim().replace(/^\{|\}$/g, "").trim();
  if (!inner) return {};
  const out: Record<string, unknown> = {};
  const parts: string[] = [];
  let depth = 0;
  let buf = "";
  for (const ch of inner) {
    if (ch === "," && depth === 0) { parts.push(buf); buf = ""; continue; }
    if (ch === "{" || ch === "[") depth++;
    if (ch === "}" || ch === "]") depth--;
    buf += ch;
  }
  if (buf.trim()) parts.push(buf);
  for (const p of parts) {
    const idx = p.indexOf(":");
    if (idx < 0) return null;
    const key = p.slice(0, idx).trim().replace(/^["']|["']$/g, "");
    out[key] = parseScalar(p.slice(idx + 1));
  }
  return out;
}

function parseSimpleYaml(text: string): unknown {
  const lines = text.split("\n");
  const result: Record<string, unknown> = {};
  let currentList: unknown[] | null = null;
  let currentItem: Record<string, unknown> | null = null;
  let listBaseIndent = -1;

  const flushItem = () => {
    if (currentItem && currentList) currentList.push(currentItem);
    currentItem = null;
  };
  const closeList = () => {
    flushItem();
    currentList = null;
    listBaseIndent = -1;
  };

  for (const raw of lines) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const indent = raw.match(/^(\s*)/)?.[1].length ?? 0;
    const line = raw.trim();

    if (line.startsWith("-") && currentList) {
      if (listBaseIndent < 0) listBaseIndent = indent;
      if (indent >= listBaseIndent) {
        flushItem();
        const content = line.replace(/^-\s*/, "");
        if (content.startsWith("{")) {
          const obj = parseInlineObject(content);
          if (obj) currentList.push(obj);
          continue;
        }
        currentItem = {};
        const m = content.match(/^([\w-]+)\s*:\s*(.*)$/);
        if (m) currentItem[m[1]] = parseScalar(m[2]);
        continue;
      }
      closeList();
    }

    if (currentItem && currentList && indent > listBaseIndent) {
      const m = line.match(/^([\w-]+)\s*:\s*(.*)$/);
      if (m) currentItem[m[1]] = parseScalar(m[2]);
      continue;
    }

    if (currentList && indent <= listBaseIndent) closeList();
    const m = line.match(/^([\w-]+)\s*:\s*(.*)$/);
    if (!m) continue;
    const [, key, val] = m;
    const trimVal = val.trim();

    if (trimVal === "" || trimVal === "|") {
      currentList = [];
      listBaseIndent = -1;
      result[key] = currentList;
    } else if (trimVal.startsWith("[")) {
      try { result[key] = JSON.parse(trimVal); } catch { result[key] = trimVal; }
    } else {
      result[key] = parseScalar(trimVal);
    }
  }
  closeList();
  return result;
}

function coerceDataPoints(raw: unknown, xKey?: string, yKey?: string): DataPoint[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const obj = item as Record<string, unknown>;
    const xVal = obj[xKey ?? "x"] ?? obj["x"] ?? obj["label"] ?? obj["name"];
    const yVal = obj[yKey ?? "y"] ?? obj["y"] ?? obj["value"] ?? obj["count"] ?? 0;
    const x = xVal == null ? "" : String(xVal);
    const y = Number(yVal);
    if (!Number.isFinite(y)) return [];
    return [{ x, y }];
  });
}

// ── YAML spec → mermaid source ──────────────────────────────────────────────

function escapeMermaidLabel(s: string): string {
  return s.replace(/"/g, "'");
}

function specToMermaid(spec: ChartSpec): string {
  const points = coerceDataPoints(spec.data, spec.x, spec.y);
  const type = spec.type ?? "bar";
  const title = spec.title ?? "";

  if (!points.length) return "";

  if (type === "pie") {
    const header = title ? `pie title ${title}` : "pie";
    const rows = points.map((p) => `    "${escapeMermaidLabel(p.x)}" : ${p.y}`);
    return [header, ...rows].join("\n");
  }

  const labels = points.map((p) => `"${escapeMermaidLabel(p.x)}"`).join(", ");
  const values = points.map((p) => p.y).join(", ");
  const maxY = Math.max(...points.map((p) => p.y), 0);
  const yAxisLabel = spec.y ? `"${escapeMermaidLabel(spec.y)}"` : `"value"`;
  const titleLine = title ? `    title "${escapeMermaidLabel(title)}"` : "";
  const seriesKw = type === "line" ? "line" : "bar";

  return [
    "xychart-beta",
    titleLine,
    `    x-axis [${labels}]`,
    `    y-axis ${yAxisLabel} 0 --> ${Math.ceil(maxY * 1.1) || 1}`,
    `    ${seriesKw} [${values}]`,
  ]
    .filter(Boolean)
    .join("\n");
}

// ── Component ────────────────────────────────────────────────────────────────

export default function ChartBlock({ code }: { code: string }) {
  const { mermaidSrc, error } = useMemo(() => {
    try {
      const spec = parseSimpleYaml(code) as ChartSpec;
      const src = specToMermaid(spec);
      if (!src) return { mermaidSrc: "", error: "No data points to chart" };
      return { mermaidSrc: src, error: null };
    } catch (e) {
      return {
        mermaidSrc: "",
        error: e instanceof Error ? e.message : String(e),
      };
    }
  }, [code]);

  if (error) {
    return (
      <pre className="mermaid-error">
        <code>{`chart error: ${error}\n\n${code}`}</code>
      </pre>
    );
  }

  return <MermaidBlock code={mermaidSrc} />;
}
