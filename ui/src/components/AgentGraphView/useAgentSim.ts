import { useRef } from "react";
import type { AgentGraphData } from "../../api";
import {
  REPULSION_K, SPRING_K, REST_LEN, GRAVITY, DAMPING, ENERGY_STOP,
  type SimNode,
} from "./types";
import { drawGraph } from "./drawGraph";

/**
 * useAgentSim — encapsulates the force-directed physics simulation refs and
 * the RAF-based tick/draw loop. Returns imperative handles for initSim,
 * startRAF, and the draw function so the parent component can drive the sim
 * without owning its internals.
 */
export function useAgentSim(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  offsetRef: React.MutableRefObject<{ x: number; y: number }>,
  scaleRef: React.MutableRefObject<number>,
  hoverRef: React.MutableRefObject<number | null>,
) {
  const nodesRef = useRef<SimNode[]>([]);
  const graphRef = useRef<AgentGraphData | null>(null);
  const rafRef = useRef<number | null>(null);
  const runningRef = useRef(false);
  const settledRef = useRef(false);

  function draw(canvas: HTMLCanvasElement, g: AgentGraphData, nodes: SimNode[]) {
    drawGraph(canvas, g, nodes, offsetRef.current, scaleRef.current, hoverRef.current, settledRef.current);
  }

  function startRAF() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  }

  function tick() {
    if (!runningRef.current) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const g = graphRef.current;
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

    // repulsion
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

    // springs along edges
    for (const e of g.edges) {
      const ai = idx.get(e.source); const bi = idx.get(e.target);
      if (ai === undefined || bi === undefined) continue;
      const dx = nodes[bi].x - nodes[ai].x;
      const dy = nodes[bi].y - nodes[ai].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (dist - REST_LEN) * SPRING_K;
      const nx = dx / dist; const ny = dy / dist;
      fx[ai] += f * nx; fy[ai] += f * ny;
      fx[bi] -= f * nx; fy[bi] -= f * ny;
    }

    // gravity
    for (let i = 0; i < nodes.length; i++) {
      fx[i] += (cx - nodes[i].x) * GRAVITY;
      fy[i] += (cy - nodes[i].y) * GRAVITY;
    }

    // integrate
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

    draw(canvas, g, nodes);

    if (runningRef.current) {
      rafRef.current = requestAnimationFrame(tick);
    } else {
      draw(canvas, g, nodes);
    }
  }

  function initSim(g: AgentGraphData, canvas: HTMLCanvasElement | null) {
    const w = canvas?.width ?? 800;
    const h = canvas?.height ?? 600;
    graphRef.current = g;
    nodesRef.current = g.nodes.map((n) => ({
      ...n,
      // pin the agent hub to the centre for a stable layout
      x: n.type === "agent" ? w / 2 : w * 0.2 + Math.random() * w * 0.6,
      y: n.type === "agent" ? h / 2 : h * 0.2 + Math.random() * h * 0.6,
      vx: 0, vy: 0,
      pinned: n.type === "agent",
    }));
    settledRef.current = false;
    runningRef.current = true;
    startRAF();
  }

  function stopRAF() {
    runningRef.current = false;
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
  }

  return { nodesRef, graphRef, rafRef, runningRef, settledRef, initSim, startRAF, stopRAF, draw };
}
