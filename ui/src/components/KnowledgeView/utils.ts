// Pure helpers for KnowledgeView: colors, geometry, edge merging.

import type { MergedEdgeGroup } from "./types";

export const TYPE_COLORS: Record<string, string> = {
  person: "#c9a84c",
  project: "#b87333",
  concept: "#7a5e9e",
  technology: "#5e7a9e",
  decision: "#9e4a3a",
  resource: "#4a9e7a",
};
const DEFAULT_COLOR = "#7a9e7e";

export function typeColor(t: string) {
  return TYPE_COLORS[t] ?? DEFAULT_COLOR;
}

export function nodeRadius(degree: number) {
  return Math.max(3, Math.min(10, 3 + Math.log(degree + 1) * 1.8));
}

export function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

export function buildMergedEdges(edges: Array<{ source: number | string; target: number | string; relation?: string }>): MergedEdgeGroup[] {
  const groups = new Map<string, MergedEdgeGroup>();
  for (const e of edges) {
    const s = Number(e.source);
    const t = Number(e.target);
    const lo = Math.min(s, t);
    const hi = Math.max(s, t);
    const key = `${lo}|${hi}`;
    if (!groups.has(key)) {
      groups.set(key, { nodeA: lo, nodeB: hi, relations: [] });
    }
    groups.get(key)!.relations.push({
      label: e.relation || "",
      from: s,
      to: t,
    });
  }
  return Array.from(groups.values());
}
