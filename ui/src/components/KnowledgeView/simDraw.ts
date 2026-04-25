// Canvas drawing and force-directed simulation step — split from useSubgraphSim.ts.

import type { SubgraphData } from "../../api";
import type { SimNode, MergedEdgeGroup } from "./types";
import { nodeRadius } from "./utils";
import { shortenEdge } from "../graphEdgeUtils";
import type { SubgraphSimRefs } from "./useSubgraphSim";

/** Draw the canvas contents. Exported so the resize observer can call it. */
export function drawCanvas(
  canvas: HTMLCanvasElement,
  _sg: SubgraphData,
  nodes: SimNode[],
  refs: Pick<SubgraphSimRefs, "offsetRef" | "scaleRef" | "selectedNodeRef" | "settledRef" | "mergedEdgesRef" | "highlightNodesRef">,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { x: ox, y: oy } = refs.offsetRef.current;
  const sc = refs.scaleRef.current;
  const selNode = refs.selectedNodeRef.current;
  const style = getComputedStyle(canvas);
  const border = style.getPropertyValue("--border").trim() || "#2c3037";
  const fgDim = style.getPropertyValue("--fg-dim").trim() || "#a39d92";
  const fg = style.getPropertyValue("--fg").trim() || "#ece8e1";
  const accent = style.getPropertyValue("--accent").trim() || "#d4855c";
  const bgPanel = style.getPropertyValue("--bg-panel").trim() || "#1d2025";

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.save();
  ctx.translate(ox, oy);
  ctx.scale(sc, sc);

  const idx = new Map<number, number>();
  nodes.forEach((n, i) => idx.set(n.id, i));

  const highlighted = refs.highlightNodesRef.current;
  const merged = refs.mergedEdgesRef.current;

  // Determine if anything is "focused" — a selected node or search highlights
  const hasFocus = selNode !== null || highlighted.size > 0;

  // Build set of all "active" node indices: selected + search + their 1-hop neighbors
  const activeNodes = new Set<number>();
  if (hasFocus) {
    if (selNode !== null) activeNodes.add(selNode);
    for (const hi of highlighted) activeNodes.add(hi);
    // Add 1-hop neighbors
    for (const g of merged) {
      const ai = idx.get(g.nodeA);
      const bi = idx.get(g.nodeB);
      if (ai === undefined || bi === undefined) continue;
      if (activeNodes.has(ai)) activeNodes.add(bi);
      if (activeNodes.has(bi)) activeNodes.add(ai);
    }
  }

  // Alpha for inactive elements when something is focused
  const dimAlpha = 0.08;

  // Viewport bounds (in graph coords) for culling
  const vl = -ox / sc - 60;
  const vt = -oy / sc - 60;
  const vr = (canvas.width - ox) / sc + 60;
  const vb = (canvas.height - oy) / sc + 60;

  // Draw merged edges — single straight line per node pair
  for (let gi = 0; gi < merged.length; gi++) {
    const g = merged[gi];
    const ai = idx.get(g.nodeA);
    const bi = idx.get(g.nodeB);
    if (ai === undefined || bi === undefined) continue;

    // Viewport cull
    const na = nodes[ai];
    const nb = nodes[bi];
    if ((na.x < vl && nb.x < vl) || (na.x > vr && nb.x > vr)) continue;
    if ((na.y < vt && nb.y < vt) || (na.y > vb && nb.y > vb)) continue;

    // Edge is active only if both endpoints are active
    const isActive = !hasFocus || (activeNodes.has(ai) && activeNodes.has(bi));

    const rA = nodeRadius(na.degree);
    const rB = nodeRadius(nb.degree);
    const shortened = shortenEdge(na.x, na.y, nb.x, nb.y, rB + 4, rA);

    ctx.strokeStyle = isActive && hasFocus ? accent : border;
    ctx.lineWidth = isActive && hasFocus ? 1.5 : 0.7;
    ctx.globalAlpha = isActive ? (hasFocus ? 1 : 0.4) : dimAlpha;
    ctx.beginPath();
    ctx.moveTo(shortened.sx, shortened.sy);
    ctx.lineTo(shortened.ex, shortened.ey);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  // Draw nodes with viewport culling
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (n.x < vl || n.x > vr || n.y < vt || n.y > vb) continue;
    const r = nodeRadius(n.degree);
    // Import typeColor inline to avoid circular dep — recompute from TYPE_COLORS
    const TYPE_COLORS: Record<string, string> = {
      person: "#c9a84c", project: "#b87333", concept: "#7a5e9e",
      technology: "#5e7a9e", decision: "#9e4a3a", resource: "#4a9e7a",
    };
    const color = TYPE_COLORS[n.type] ?? "#7a9e7e";
    const isActive = !hasFocus || activeNodes.has(i);
    const isCore = selNode === i || highlighted.has(i);
    ctx.globalAlpha = isActive ? 1 : dimAlpha;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + (isCore ? 3 : 0), 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    if (isActive && hasFocus) {
      ctx.strokeStyle = isCore ? accent : fg;
      ctx.lineWidth = isCore ? 2.5 : 1;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // Labels — only after simulation settles
  if (refs.settledRef.current) {
    ctx.font = "9px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.x < vl || n.x > vr || n.y < vt || n.y > vb) continue;
      const isActive = !hasFocus || activeNodes.has(i);
      const isCore = selNode === i || highlighted.has(i);
      // When focused: only label active nodes. When unfocused: show labels for degree >= 3
      if (hasFocus) {
        if (!isActive) continue;
      } else {
        if (n.degree < 3) continue;
      }
      const r = nodeRadius(n.degree) + (isCore ? 3 : 0);
      const label = n.name.length > 22 ? n.name.slice(0, 21) + "…" : n.name;
      const metrics = ctx.measureText(label);
      ctx.globalAlpha = isActive ? 0.9 : dimAlpha;
      ctx.fillStyle = bgPanel;
      ctx.fillRect(n.x - metrics.width / 2 - 3, n.y + r + 3, metrics.width + 6, 12);
      ctx.globalAlpha = 1;
      ctx.fillStyle = isCore ? fg : fgDim;
      ctx.fillText(label, n.x, n.y + r + 4);
    }
  }

  ctx.restore();
}

/** Runs one frame of force-directed simulation. Returns false when settled. */
export function simStep(
  nodes: SimNode[],
  merged: MergedEdgeGroup[],
  cx: number,
  cy: number,
  canvas: HTMLCanvasElement,
): boolean {
  const n = nodes.length;
  const STEPS_PER_FRAME = n > 300 ? 4 : n > 100 ? 3 : 2;

  // Adaptive parameters based on graph density
  const avgDegree = n > 0 ? (merged.length * 2) / n : 1;
  const repulsion = Math.max(800, 5000 / (1 + avgDegree * 0.4));
  const springRest = Math.max(50, 140 - avgDegree * 6);
  const springStr = 0.025 + avgDegree * 0.003;
  const gravity = 0.012 + avgDegree * 0.004;
  const damping = n > 200 ? 0.7 : 0.82;
  // Bounding box to prevent runaway nodes
  const bound = Math.max(canvas.width, canvas.height) * 0.45;

  for (let step = 0; step < STEPS_PER_FRAME; step++) {
    const idx = new Map<number, number>();
    nodes.forEach((nd, i) => idx.set(nd.id, i));

    const fx = new Float64Array(n);
    const fy = new Float64Array(n);

    // Grid-based repulsion: O(n·k) instead of O(n²)
    const CELL = Math.max(springRest * 1.5, 100);
    const grid = new Map<number, number[]>();
    for (let i = 0; i < n; i++) {
      const gx = Math.floor(nodes[i].x / CELL);
      const gy = Math.floor(nodes[i].y / CELL);
      const key = gx * 73856093 ^ gy * 19349663;
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key)!.push(i);
    }
    for (let i = 0; i < n; i++) {
      const gx = Math.floor(nodes[i].x / CELL);
      const gy = Math.floor(nodes[i].y / CELL);
      for (let ddx = -1; ddx <= 1; ddx++) {
        for (let ddy = -1; ddy <= 1; ddy++) {
          const nkey = (gx + ddx) * 73856093 ^ (gy + ddy) * 19349663;
          const cell = grid.get(nkey);
          if (!cell) continue;
          for (const j of cell) {
            if (j <= i) continue;
            const dx = nodes[j].x - nodes[i].x;
            const dy = nodes[j].y - nodes[i].y;
            const d2 = dx * dx + dy * dy + 1;
            const f = repulsion / d2;
            const d = Math.sqrt(d2);
            fx[i] -= f * dx / d; fy[i] -= f * dy / d;
            fx[j] += f * dx / d; fy[j] += f * dy / d;
          }
        }
      }
    }

    // Spring forces via merged edges (one spring per pair)
    for (const g of merged) {
      const ai = idx.get(g.nodeA);
      const bi = idx.get(g.nodeB);
      if (ai === undefined || bi === undefined) continue;
      const dx = nodes[bi].x - nodes[ai].x;
      const dy = nodes[bi].y - nodes[ai].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const weight = Math.min(g.relations.length, 4);
      const f = (dist - springRest) * springStr * weight;
      fx[ai] += f * dx / dist; fy[ai] += f * dy / dist;
      fx[bi] -= f * dx / dist; fy[bi] -= f * dy / dist;
    }

    // Center gravity (scaled by degree — hub nodes pulled harder)
    for (let i = 0; i < n; i++) {
      const g = gravity * (1 + Math.log(nodes[i].degree + 1) * 0.3);
      fx[i] += (cx - nodes[i].x) * g;
      fy[i] += (cy - nodes[i].y) * g;
    }

    let ke = 0;
    for (let i = 0; i < n; i++) {
      if (nodes[i].pinned) continue;
      nodes[i].vx = (nodes[i].vx + fx[i]) * damping;
      nodes[i].vy = (nodes[i].vy + fy[i]) * damping;
      nodes[i].x += nodes[i].vx;
      nodes[i].y += nodes[i].vy;
      // Clamp to bounding box
      if (nodes[i].x < cx - bound) { nodes[i].x = cx - bound; nodes[i].vx *= -0.3; }
      if (nodes[i].x > cx + bound) { nodes[i].x = cx + bound; nodes[i].vx *= -0.3; }
      if (nodes[i].y < cy - bound) { nodes[i].y = cy - bound; nodes[i].vy *= -0.3; }
      if (nodes[i].y > cy + bound) { nodes[i].y = cy + bound; nodes[i].vy *= -0.3; }
      ke += nodes[i].vx * nodes[i].vx + nodes[i].vy * nodes[i].vy;
    }

    if (ke < 0.1 * n) return false; // settled
  }
  return true; // still running
}
