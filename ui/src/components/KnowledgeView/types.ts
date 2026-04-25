// Shared types for KnowledgeView sub-components.

export interface SimNode {
  id: number; name: string; type: string; degree: number;
  x: number; y: number; vx: number; vy: number; pinned: boolean;
}

export interface MergedEdgeGroup {
  nodeA: number; // lower id
  nodeB: number; // higher id
  relations: Array<{ label: string; from: number; to: number }>;
}
