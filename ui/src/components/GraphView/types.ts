// Shared types for GraphView sub-components.

import type { GraphNode, EntityNode } from "../../api";

export type NodeType = "file" | "entity";
export type ScopeType = "all" | "file" | "folder" | "tag" | "search" | "entity";

export interface SimNode {
  id: string;
  nodeType: NodeType;
  x: number; y: number; vx: number; vy: number;
  pinned: boolean;
  data: GraphNode | null;
  entity: EntityNode | null;
}

export interface DetailInfo {
  type: "file" | "entity";
  path?: string;
  entity?: EntityNode;
}

export interface EdgeTypeConfig {
  dash: number[];
  color: string;
  alpha: number;
}

export const EDGE_STYLES: Record<string, EdgeTypeConfig> = {
  link: { dash: [], color: "", alpha: 0.5 },
  "tag-cooccurrence": { dash: [4, 4], color: "#c9a84c", alpha: 0.35 },
  "shared-entity": { dash: [6, 3], color: "#7a5e9e", alpha: 0.4 },
  "folder-cross": { dash: [2, 4], color: "#5e7a9e", alpha: 0.25 },
};

export const REPULSION_K = 3000;
export const SPRING_K    = 0.03;
export const REST_LEN    = 80;
export const GRAVITY     = 0.01;
export const DAMPING     = 0.88;
export const ENERGY_STOP = 0.15;
