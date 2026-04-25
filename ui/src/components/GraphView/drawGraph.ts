/**
 * @file Canvas rendering logic for the vault GraphView.
 *
 * Draws the knowledge graph (file and entity nodes, typed edges) directly onto
 * an `HTMLCanvasElement` 2D context. Holds no internal state — receives `DrawState`
 * and graph data on each frame, making it pure and deterministic.
 */

import type { GraphData } from "../../api";
import type { SimNode } from "./types";
import { EDGE_STYLES } from "./types";
import { folderColor, entityColor, nodeRadius } from "./utils";
import {
  buildMultiEdgeIndex,
  computeCurveOffset,
  getControlPoint,
  getCurveMidpoint,
  getArrowAngle,
  drawArrowhead,
  shortenEdge,
  drawEdgeCurve,
} from "../graphEdgeUtils";

export interface DrawState {
  offset: { x: number; y: number };
  scale: number;
  hover: number | null;
  selected: number | null;
  settled: boolean;
}

/**
 * Render the complete graph (edges, nodes, and labels) onto the given canvas.
 *
 * Applies the camera transform (`offset` + `scale`) via `ctx.save/translate/scale`.
 * Colors are read from CSS custom properties on the canvas to respect the active theme.
 * Nodes of type `"entity"` are drawn as rounded rectangles; file nodes as circles
 * (radius proportional to file size). Parallel edges are automatically curved via
 * `buildMultiEdgeIndex`. Labels and arrowheads only appear when `state.settled` is
 * `true` (force simulation has stabilized), to avoid visual clutter during layout.
 *
 * @param canvas - Target canvas element; must be visible in the DOM.
 * @param g - Graph data: nodes, edges, and orphan list.
 * @param nodes - Current positions from the force simulation (mutated by the simulator).
 * @param state - Camera state (pan/zoom) and interaction state (hover/selected/settled).
 */
export function draw(
  canvas: HTMLCanvasElement,
  g: GraphData,
  nodes: SimNode[],
  state: DrawState,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const { offset: { x: ox, y: oy }, scale: sc, hover, selected, settled } = state;
  const orphanSet = new Set(g.orphans);

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.save();
  ctx.translate(ox, oy);
  ctx.scale(sc, sc);

  const style = getComputedStyle(canvas);
  const borderColor = style.getPropertyValue("--border").trim() || "#333";
  const fgDim = style.getPropertyValue("--fg-dim").trim() || "#888";
  const accent = style.getPropertyValue("--accent").trim() || "#7c9ef8";

  const idx = new Map<string, number>();
  nodes.forEach((n, i) => idx.set(n.id, i));

  const edgeInfo = buildMultiEdgeIndex(
    g.edges,
    (e) => e.from,
    (e) => e.to,
  );

  // Draw edges
  for (let ei = 0; ei < g.edges.length; ei++) {
    const e = g.edges[ei];
    const ai = idx.get(e.from);
    const bi = idx.get(e.to);
    if (ai === undefined || bi === undefined) continue;
    const highlighted = hover === ai || hover === bi || selected === ai || selected === bi;
    const edgeType = e.type ?? "link";
    const eStyle = EDGE_STYLES[edgeType] ?? EDGE_STYLES.link;

    const info = edgeInfo.get(ei)!;
    const curveOff = computeCurveOffset(info.indexInGroup, info.count, 20);
    const isCurved = curveOff !== 0;

    const rA = nodes[ai].nodeType === "entity" ? 6 : nodeRadius((nodes[ai].data?.size ?? 0));
    const rB = nodes[bi].nodeType === "entity" ? 6 : nodeRadius((nodes[bi].data?.size ?? 0));
    const shortened = shortenEdge(nodes[ai].x, nodes[ai].y, nodes[bi].x, nodes[bi].y, rB + 3, rA);

    const cp = getControlPoint(shortened.sx, shortened.sy, shortened.ex, shortened.ey, curveOff);

    ctx.setLineDash(eStyle.dash);
    ctx.strokeStyle = highlighted ? accent : (eStyle.color || borderColor);
    ctx.lineWidth = highlighted ? 1.5 : 0.8;
    ctx.globalAlpha = highlighted ? 1 : eStyle.alpha;
    drawEdgeCurve(ctx, shortened.sx, shortened.sy, shortened.ex, shortened.ey, cp.cx, cp.cy, isCurved);

    ctx.fillStyle = highlighted ? accent : (eStyle.color || borderColor);
    const arrowAngle = isCurved
      ? getArrowAngle(cp.cx, cp.cy, shortened.ex, shortened.ey)
      : getArrowAngle(shortened.sx, shortened.sy, shortened.ex, shortened.ey);
    drawArrowhead(ctx, shortened.ex, shortened.ey, arrowAngle, highlighted ? 6 : 4);

    ctx.globalAlpha = 1;
    ctx.setLineDash([]);

    if (highlighted && settled && edgeType !== "link") {
      const { mx, my } = isCurved
        ? getCurveMidpoint(shortened.sx, shortened.sy, cp.cx, cp.cy, shortened.ex, shortened.ey)
        : { mx: (shortened.sx + shortened.ex) / 2, my: (shortened.sy + shortened.ey) / 2 };
      const label = edgeType.replace(/-/g, " ");
      ctx.font = "8px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      const m = ctx.measureText(label);
      const bgPanel = style.getPropertyValue("--bg-panel").trim() || "#1d2025";
      ctx.fillStyle = bgPanel;
      ctx.globalAlpha = 0.8;
      ctx.fillRect(mx - m.width / 2 - 2, my - 12, m.width + 4, 12);
      ctx.globalAlpha = 1;
      ctx.fillStyle = fgDim;
      ctx.fillText(label, mx, my - 2);
    }
  }

  // Draw nodes
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    const isHover = hover === i;
    const isSelected = selected === i;

    if (n.nodeType === "entity" && n.entity) {
      const color = entityColor(n.entity.type);
      const sz = 11;
      ctx.beginPath();
      ctx.roundRect(n.x - sz / 2, n.y - sz / 2, sz, sz, 3);
      ctx.fillStyle = color;
      ctx.fill();
      if (isHover || isSelected) {
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      if (settled) {
        ctx.font = "9px system-ui, sans-serif";
        ctx.fillStyle = fgDim;
        ctx.textAlign = "center";
        ctx.fillText(n.entity.name, n.x, n.y + sz / 2 + 11);
      }
    } else if (n.data) {
      const r = nodeRadius(n.data.size);
      const color = folderColor(n.data.folder);
      const isOrphan = orphanSet.has(n.data.path);

      ctx.beginPath();
      ctx.arc(n.x, n.y, r + (isHover || isSelected ? 2 : 0), 0, Math.PI * 2);

      if (isOrphan) {
        ctx.setLineDash([3, 2]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.2;
        ctx.fillStyle = color + "44";
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.fillStyle = color;
        ctx.fill();
        if (isHover || isSelected) {
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
      }

      if (settled) {
        const basename = n.data.path.split("/").pop() ?? n.data.path;
        const label = n.data.title || basename.replace(/\.mdx?$/, "");
        ctx.font = "10px system-ui, sans-serif";
        ctx.fillStyle = fgDim;
        ctx.textAlign = "center";
        ctx.fillText(label, n.x, n.y + r + 12);
      }
    }
  }

  ctx.restore();
}
