// Custom hook for KnowledgeView: force-directed simulation over subgraph nodes.

import { useEffect, useRef } from "react";
import type { SubgraphData } from "../../api";
import type { SimNode, MergedEdgeGroup } from "./types";
import { buildMergedEdges } from "./utils";
import { drawCanvas, simStep } from "./simDraw";

// Re-export drawCanvas so existing importers (SubgraphCanvas, index) keep working.
export { drawCanvas };

/** All mutable sim state threaded through refs — stable across renders. */
export interface SubgraphSimRefs {
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  simNodesRef: React.MutableRefObject<SimNode[]>;
  subgraphRef: React.MutableRefObject<SubgraphData | null>;
  mergedEdgesRef: React.MutableRefObject<MergedEdgeGroup[]>;
  rafRef: React.MutableRefObject<number | null>;
  runningRef: React.MutableRefObject<boolean>;
  tickRef: React.MutableRefObject<number>;
  settledRef: React.MutableRefObject<boolean>;
  offsetRef: React.MutableRefObject<{ x: number; y: number }>;
  scaleRef: React.MutableRefObject<number>;
  hoverRef: React.MutableRefObject<number | null>;
  selectedNodeRef: React.MutableRefObject<number | null>;
  selectedEdgeRef: React.MutableRefObject<number | null>;
  hoveredEdgeGroupRef: React.MutableRefObject<number | null>;
  dragRef: React.MutableRefObject<{ idx: number; moved: boolean } | null>;
  panRef: React.MutableRefObject<{ ox: number; oy: number; mx: number; my: number; moved: boolean } | null>;
  edgeTooltipRef: React.MutableRefObject<HTMLDivElement | null>;
  highlightNodesRef: React.MutableRefObject<Set<number>>;
}

export function useSubgraphSimRefs(): SubgraphSimRefs {
  return {
    canvasRef: useRef<HTMLCanvasElement>(null),
    simNodesRef: useRef<SimNode[]>([]),
    subgraphRef: useRef<SubgraphData | null>(null),
    mergedEdgesRef: useRef<MergedEdgeGroup[]>([]),
    rafRef: useRef<number | null>(null),
    runningRef: useRef(false),
    tickRef: useRef(0),
    settledRef: useRef(false),
    offsetRef: useRef({ x: 0, y: 0 }),
    scaleRef: useRef(1),
    hoverRef: useRef<number | null>(null),
    selectedNodeRef: useRef<number | null>(null),
    selectedEdgeRef: useRef<number | null>(null),
    hoveredEdgeGroupRef: useRef<number | null>(null),
    dragRef: useRef<{ idx: number; moved: boolean } | null>(null),
    panRef: useRef<{ ox: number; oy: number; mx: number; my: number; moved: boolean } | null>(null),
    edgeTooltipRef: useRef<HTMLDivElement | null>(null),
    highlightNodesRef: useRef<Set<number>>(new Set()),
  };
}

/** Hook that wires subgraph data changes into the simulation loop and canvas draw. */
export function useSubgraphSim(
  subgraphData: SubgraphData | null,
  refs: SubgraphSimRefs,
  fitGraph: () => void,
) {
  function startRAF() {
    if (refs.rafRef.current !== null) cancelAnimationFrame(refs.rafRef.current);
    refs.rafRef.current = requestAnimationFrame(tick);
  }

  function tick() {
    if (!refs.runningRef.current) return;
    const canvas = refs.canvasRef.current;
    const sg = refs.subgraphRef.current;
    const nodes = refs.simNodesRef.current;
    const merged = refs.mergedEdgesRef.current;
    if (!canvas || !sg || nodes.length === 0) return;

    const cx = canvas.width / 2;
    const cy = canvas.height / 2;

    refs.tickRef.current++;
    const stillRunning = refs.tickRef.current < 300
      ? simStep(nodes, merged, cx, cy, canvas)
      : false;

    drawCanvas(canvas, sg, nodes, refs);

    // Auto-fit on first settle
    if (!stillRunning && refs.tickRef.current <= 310) {
      refs.settledRef.current = true;
      refs.runningRef.current = false;
      fitGraph();
      drawCanvas(canvas, sg, nodes, refs);
      return;
    }

    if (refs.runningRef.current) refs.rafRef.current = requestAnimationFrame(tick);
    else drawCanvas(canvas, sg, nodes, refs);
  }

  // Wire subgraph data changes into the simulation
  useEffect(() => {
    if (!subgraphData) return;
    refs.subgraphRef.current = subgraphData;
    const canvas = refs.canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
    }
    const w = canvas.width || 800;
    const h = canvas.height || 600;
    refs.simNodesRef.current = subgraphData.nodes.map((n) => ({
      ...n,
      x: w * 0.2 + Math.random() * w * 0.6,
      y: h * 0.2 + Math.random() * h * 0.6,
      vx: 0, vy: 0, pinned: false,
    }));
    refs.mergedEdgesRef.current = buildMergedEdges(subgraphData.edges);
    // Hide tooltip on data change
    if (refs.edgeTooltipRef.current) refs.edgeTooltipRef.current.style.display = "none";
    refs.hoveredEdgeGroupRef.current = null;
    refs.settledRef.current = false;
    refs.runningRef.current = true;
    refs.tickRef.current = 0;
    startRAF();
  }, [subgraphData]);

  // Cleanup RAF on unmount
  useEffect(() => {
    return () => {
      refs.runningRef.current = false;
      if (refs.rafRef.current !== null) cancelAnimationFrame(refs.rafRef.current);
    };
  }, []);

  return { startRAF };
}
