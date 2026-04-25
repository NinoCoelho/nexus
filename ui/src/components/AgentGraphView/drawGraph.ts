import type { AgentGraphData, AgentGraphNode } from "../../api";
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
import { nodeRadius, truncate, type SimNode } from "./types";

export function drawGraph(
  canvas: HTMLCanvasElement,
  g: AgentGraphData,
  nodes: SimNode[],
  offsetRef: { x: number; y: number },
  scaleRef: number,
  hoverIdx: number | null,
  settled: boolean,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const { x: ox, y: oy } = offsetRef;
  const sc = scaleRef;
  const hover = hoverIdx;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.save();
  ctx.translate(ox, oy);
  ctx.scale(sc, sc);

  const style = getComputedStyle(canvas);
  const border = style.getPropertyValue("--border").trim() || "#2c3037";
  const fgDim = style.getPropertyValue("--fg-dim").trim() || "#a39d92";
  const fg = style.getPropertyValue("--fg").trim() || "#ece8e1";
  const accent = style.getPropertyValue("--accent").trim() || "#d4855c";
  const sage = style.getPropertyValue("--sage").trim() || "#8ba888";
  const bgPanel = style.getPropertyValue("--bg-panel").trim() || "#1d2025";

  const colorFor = (t: AgentGraphNode["type"]) =>
    t === "agent" ? accent : t === "skill" ? sage : fgDim;

  const idx = new Map<string, number>();
  nodes.forEach((n, i) => idx.set(n.id, i));

  // edges
  const edgeInfo = buildMultiEdgeIndex(
    g.edges,
    (e) => e.source,
    (e) => e.target,
  );

  for (let ei = 0; ei < g.edges.length; ei++) {
    const e = g.edges[ei];
    const ai = idx.get(e.source); const bi = idx.get(e.target);
    if (ai === undefined || bi === undefined) continue;
    const highlighted = hover === ai || hover === bi;

    const info = edgeInfo.get(ei)!;
    const curveOff = computeCurveOffset(info.indexInGroup, info.count, 18);
    const isCurved = curveOff !== 0;

    const rA = nodeRadius(nodes[ai].type);
    const rB = nodeRadius(nodes[bi].type);
    const shortened = shortenEdge(nodes[ai].x, nodes[ai].y, nodes[bi].x, nodes[bi].y, rB + 3, rA);

    const cp = getControlPoint(shortened.sx, shortened.sy, shortened.ex, shortened.ey, curveOff);

    ctx.strokeStyle = highlighted ? accent : border;
    ctx.lineWidth = highlighted ? 1.5 : 0.8;
    ctx.globalAlpha = highlighted ? 1 : 0.5;
    drawEdgeCurve(ctx, shortened.sx, shortened.sy, shortened.ex, shortened.ey, cp.cx, cp.cy, isCurved);

    ctx.fillStyle = highlighted ? accent : border;
    const arrowAngle = isCurved
      ? getArrowAngle(cp.cx, cp.cy, shortened.ex, shortened.ey)
      : getArrowAngle(shortened.sx, shortened.sy, shortened.ex, shortened.ey);
    drawArrowhead(ctx, shortened.ex, shortened.ey, arrowAngle, highlighted ? 6 : 4);

    ctx.globalAlpha = 1;

    if (highlighted && settled && e.label) {
      const { mx, my } = isCurved
        ? getCurveMidpoint(shortened.sx, shortened.sy, cp.cx, cp.cy, shortened.ex, shortened.ey)
        : { mx: (shortened.sx + shortened.ex) / 2, my: (shortened.sy + shortened.ey) / 2 };
      ctx.font = "8px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      const m = ctx.measureText(e.label);
      ctx.fillStyle = bgPanel;
      ctx.globalAlpha = 0.8;
      ctx.fillRect(mx - m.width / 2 - 2, my - 12, m.width + 4, 12);
      ctx.globalAlpha = 1;
      ctx.fillStyle = fgDim;
      ctx.fillText(e.label, mx, my - 2);
    }
  }

  // nodes
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    const r = nodeRadius(n.type);
    const color = colorFor(n.type);
    const isHover = hover === i;

    ctx.beginPath();
    ctx.arc(n.x, n.y, r + (isHover ? 2 : 0), 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    if (isHover) {
      ctx.strokeStyle = fg;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  // labels — agents + skills when settled; sessions only on hover
  if (settled) {
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const isHover = hover === i;
      if (n.type === "session" && !isHover) continue;
      const r = nodeRadius(n.type);
      const label = truncate(n.label, 24);
      const tx = n.x;
      const ty = n.y + r + 4;
      // halo background so labels don't collide with nodes/edges
      const metrics = ctx.measureText(label);
      const padX = 3; const padY = 1;
      ctx.fillStyle = bgPanel;
      ctx.globalAlpha = 0.85;
      ctx.fillRect(
        tx - metrics.width / 2 - padX,
        ty - padY,
        metrics.width + padX * 2,
        12 + padY,
      );
      ctx.globalAlpha = 1;
      ctx.fillStyle = n.type === "agent" ? fg : fgDim;
      ctx.fillText(label, tx, ty);
    }
  }

  ctx.restore();
}
