// Shared types for the unified 3D graph.

export type ModeId = "knowledge" | "vault" | "agent";

export type GeometryKind = "sphere" | "octahedron" | "icosahedron" | "box";

/**
 * Mode-agnostic node passed to the single shared ForceGraph3D.
 * The `meta` blob is owned by the mode adapter; the canvas never reads it.
 */
export interface UnifiedNode {
  id: string;
  label: string;
  kind: string;
  degree: number;
  color?: string;
  geometry?: GeometryKind;
  radiusBoost?: number;
  meta?: unknown;
}

export interface UnifiedRelation {
  from: string;
  to: string;
  label: string;
}

export interface UnifiedLink {
  source: string | UnifiedNode;
  target: string | UnifiedNode;
  kind: string;
  color?: string;
  relations?: UnifiedRelation[];
  meta?: unknown;
}

export interface UnifiedGraphData {
  nodes: UnifiedNode[];
  links: UnifiedLink[];
}

export interface ContextMenuItem {
  label: string;
  onClick: () => void;
}

/** Imperative handle exposed by GraphCanvas3D for toolbar buttons. */
export interface GraphCanvasHandle {
  fit: () => void;
  reheat: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  flyTo: (nodeId: string) => void;
}
