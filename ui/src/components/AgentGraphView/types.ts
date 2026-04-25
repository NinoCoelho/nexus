import type { AgentGraphNode } from "../../api";

// ── sim node ──────────────────────────────────────────────────────────────────
export interface SimNode extends AgentGraphNode {
  x: number; y: number; vx: number; vy: number;
  pinned: boolean;
}

// ── physics constants ─────────────────────────────────────────────────────────
export const REPULSION_K = 3000;
export const SPRING_K    = 0.03;
export const REST_LEN    = 90;
export const GRAVITY     = 0.01;
export const DAMPING     = 0.88;
export const ENERGY_STOP = 0.15;

// ── node sizing by type ───────────────────────────────────────────────────────
export function nodeRadius(type: AgentGraphNode["type"]): number {
  if (type === "agent") return 14;
  if (type === "skill") return 8;
  return 5; // session
}

export function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}
