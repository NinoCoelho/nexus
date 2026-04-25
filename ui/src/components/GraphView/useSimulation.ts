// Force-directed simulation hook for GraphView.
// Manages physics tick loop (repulsion, spring, gravity) and RAF scheduling.

import { useRef } from "react";
import type { GraphData } from "../../api";
import type { SimNode } from "./types";
import { REPULSION_K, SPRING_K, REST_LEN, GRAVITY, DAMPING, ENERGY_STOP } from "./types";
import { draw, type DrawState } from "./drawGraph";

export interface SimRefs {
  nodesRef: React.MutableRefObject<SimNode[]>;
  rafRef: React.MutableRefObject<number | null>;
  runningRef: React.MutableRefObject<boolean>;
  settledRef: React.MutableRefObject<boolean>;
}

export function useSimulation(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  offsetRef: React.MutableRefObject<{ x: number; y: number }>,
  scaleRef: React.MutableRefObject<number>,
  hoverRef: React.MutableRefObject<number | null>,
  selectedRef: React.MutableRefObject<number | null>,
  getFilteredGraph: () => GraphData | null,
): SimRefs & { initSim: (g: GraphData, canvas: HTMLCanvasElement | null) => void } {
  const nodesRef   = useRef<SimNode[]>([]);
  const rafRef     = useRef<number | null>(null);
  const runningRef = useRef(false);
  const settledRef = useRef(false);

  function getDrawState(): DrawState {
    return {
      offset: offsetRef.current,
      scale: scaleRef.current,
      hover: hoverRef.current,
      selected: selectedRef.current,
      settled: settledRef.current,
    };
  }

  function startRAF() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  }

  function tick() {
    if (!runningRef.current) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const g = getFilteredGraph();
    const nodes = nodesRef.current;

    if (!g || nodes.length === 0) {
      rafRef.current = requestAnimationFrame(tick);
      return;
    }

    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const idx = new Map<string, number>();
    nodes.forEach((n, i) => idx.set(n.id, i));

    const fx = new Float64Array(nodes.length);
    const fy = new Float64Array(nodes.length);

    // Repulsion between all node pairs
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist2 = dx * dx + dy * dy + 1;
        const f = REPULSION_K / dist2;
        const dist = Math.sqrt(dist2);
        const nx = dx / dist; const ny = dy / dist;
        fx[i] -= f * nx; fy[i] -= f * ny;
        fx[j] += f * nx; fy[j] += f * ny;
      }
    }

    // Spring forces along edges
    for (const e of g.edges) {
      const ai = idx.get(e.from); const bi = idx.get(e.to);
      if (ai === undefined || bi === undefined) continue;
      const dx = nodes[bi].x - nodes[ai].x;
      const dy = nodes[bi].y - nodes[ai].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (dist - REST_LEN) * SPRING_K;
      const nx = dx / dist; const ny = dy / dist;
      fx[ai] += f * nx; fy[ai] += f * ny;
      fx[bi] -= f * nx; fy[bi] -= f * ny;
    }

    // Gravity toward center
    for (let i = 0; i < nodes.length; i++) {
      fx[i] += (cx - nodes[i].x) * GRAVITY;
      fy[i] += (cy - nodes[i].y) * GRAVITY;
    }

    // Integrate velocities and positions
    let totalKE = 0;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].pinned) continue;
      nodes[i].vx = (nodes[i].vx + fx[i]) * DAMPING;
      nodes[i].vy = (nodes[i].vy + fy[i]) * DAMPING;
      nodes[i].x += nodes[i].vx;
      nodes[i].y += nodes[i].vy;
      totalKE += nodes[i].vx * nodes[i].vx + nodes[i].vy * nodes[i].vy;
    }

    if (totalKE < ENERGY_STOP) {
      settledRef.current = true;
      runningRef.current = false;
    }

    draw(canvas, g, nodes, getDrawState());

    if (runningRef.current) {
      rafRef.current = requestAnimationFrame(tick);
    } else {
      draw(canvas, g, nodes, getDrawState());
    }
  }

  function initSim(g: GraphData, canvas: HTMLCanvasElement | null) {
    const w = canvas?.width ?? 800;
    const h = canvas?.height ?? 600;
    const simNodes: SimNode[] = [];

    for (const n of g.nodes) {
      simNodes.push({
        id: n.path,
        nodeType: "file",
        x: w * 0.2 + Math.random() * w * 0.6,
        y: h * 0.2 + Math.random() * h * 0.6,
        vx: 0, vy: 0, pinned: false,
        data: n, entity: null,
      });
    }

    const entityNodes = g.entity_nodes ?? [];
    for (const en of entityNodes) {
      simNodes.push({
        id: `entity:${en.id}`,
        nodeType: "entity",
        x: w * 0.2 + Math.random() * w * 0.6,
        y: h * 0.2 + Math.random() * h * 0.6,
        vx: 0, vy: 0, pinned: false,
        data: null, entity: en,
      });
    }

    nodesRef.current = simNodes;
    settledRef.current = false;
    runningRef.current = true;
    selectedRef.current = null;
    startRAF();
  }

  return { nodesRef, rafRef, runningRef, settledRef, initSim };
}
