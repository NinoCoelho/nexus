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

import { useEffect, useMemo } from "react";
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

// LLM-authored widgets sometimes spell these as JSON with `chartType`,
// `xField`, `yField` instead of the YAML `type` / `x` / `y` we document.
// Accept both rather than failing silently with "No data points to chart".
function normalizeSpec(raw: unknown): ChartSpec {
  if (!raw || typeof raw !== "object") return {};
  const r = raw as Record<string, unknown>;
  const pick = <T,>(...keys: string[]): T | undefined => {
    for (const k of keys) {
      const v = r[k];
      if (v !== undefined && v !== null) return v as T;
    }
    return undefined;
  };
  return {
    type: pick<ChartSpec["type"]>("type", "chartType", "kind"),
    title: pick<string>("title"),
    x: pick<string>("x", "xField", "xKey", "xAxis"),
    y: pick<string>("y", "yField", "yKey", "yAxis"),
    data: pick<unknown[]>("data", "rows", "points"),
  };
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

// Curated 8-color palette used for every nexus-chart bar/line/pie. Hues are
// spaced ~45° apart on a Tailwind-ish wheel so adjacent bars always read as
// distinct, and saturation/lightness sit in a band that survives both light
// and dark backgrounds (mermaid renders charts on a transparent SVG so we
// don't need separate light/dark sets — the band is the compromise).
//
// Default mermaid xychart palette is a single muted grey; without an init
// directive every bar comes out the same washed-out colour. The init block
// below threads this palette through ``xyChart.plotColorPalette`` (bars +
// lines) and ``pie1..pie8`` (pie slices).
const CHART_PALETTE = [
  "#3b82f6", // blue
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ef4444", // red
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#14b8a6", // teal
  "#f97316", // orange
];

function chartInitDirective(): string {
  const palette = CHART_PALETTE.join(", ");
  const pieVars = CHART_PALETTE.reduce<Record<string, string>>(
    (acc, hex, i) => ({ ...acc, [`pie${i + 1}`]: hex }),
    {},
  );
  const themeVariables = {
    xyChart: { plotColorPalette: palette },
    ...pieVars,
  };
  // Mermaid expects the init JSON on a single line; the directive itself must
  // start the source so xychart-beta / pie pick it up.
  return `%%{init: ${JSON.stringify({ theme: "base", themeVariables })}}%%`;
}

function specToMermaid(spec: ChartSpec): string {
  const points = coerceDataPoints(spec.data, spec.x, spec.y);
  const type = spec.type ?? "bar";
  const title = spec.title ?? "";

  if (!points.length) return "";

  const init = chartInitDirective();

  if (type === "pie") {
    const header = title ? `pie title ${title}` : "pie";
    const rows = points.map((p) => `    "${escapeMermaidLabel(p.x)}" : ${p.y}`);
    return [init, header, ...rows].join("\n");
  }

  const labels = points.map((p) => `"${escapeMermaidLabel(p.x)}"`).join(", ");
  const values = points.map((p) => p.y).join(", ");
  const maxY = Math.max(...points.map((p) => p.y), 0);
  const yAxisLabel = spec.y ? `"${escapeMermaidLabel(spec.y)}"` : `"value"`;
  const titleLine = title ? `    title "${escapeMermaidLabel(title)}"` : "";
  const seriesKw = type === "line" ? "line" : "bar";

  return [
    init,
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

function parseSpec(code: string): ChartSpec {
  // Try JSON first — LLMs often emit JSON-shaped specs even when we asked
  // for YAML. The leading-brace check avoids paying JSON.parse on every
  // YAML body just to fail.
  const trimmed = code.trim();
  if (trimmed.startsWith("{")) {
    try {
      return normalizeSpec(JSON.parse(trimmed));
    } catch {
      // fall through to YAML
    }
  }
  return normalizeSpec(parseSimpleYaml(code));
}

interface ChartBlockProps {
  code: string;
  /** Notified when the spec fails to parse or yields no plottable data, so a
   *  wrapping container (e.g. WidgetGrid) can offer a friendly error UI with
   *  Edit / Refine actions instead of just rendering raw text. */
  onError?: (message: string) => void;
}

export default function ChartBlock({ code, onError }: ChartBlockProps) {
  const { mermaidSrc, error } = useMemo(() => {
    try {
      const spec = parseSpec(code);
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

  // Fire onError as a side effect so the parent can react. We avoid calling
  // it during render — React would flag the state update as out-of-tree.
  useEffect(() => {
    if (error && onError) onError(error);
  }, [error, onError]);

  if (error) {
    return (
      <pre className="mermaid-error">
        <code>{`chart error: ${error}\n\n${code}`}</code>
      </pre>
    );
  }

  return <MermaidBlock code={mermaidSrc} />;
}
