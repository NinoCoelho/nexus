/**
 * graphEdgeUtils — edge layout helpers for Cytoscape graph views.
 *
 * When multiple edges exist between the same node pair (e.g. a "link" edge
 * and a "tag-cooccurrence" edge), we need curve offsets so they don't
 * overlap. This module computes:
 *   - Multi-edge index (which parallel edge is this?)
 *   - Curve offset direction (above/below the straight line)
 *   - Bezier control point distances
 *
 * Shared by GraphView (vault graph) and AgentGraphView (agent graph).
 */

export interface MultiEdgeInfo {
  count: number;
  indexInGroup: number;
}

export function buildMultiEdgeIndex<S, T>(
  edges: Array<S>,
  getSource: (e: S) => T,
  getTarget: (e: S) => T,
): Map<number, MultiEdgeInfo> {
  const pairOrder = new Map<string, { indices: number[]; forward: boolean[] }>();
  for (let i = 0; i < edges.length; i++) {
    const s = getSource(edges[i]);
    const t = getTarget(edges[i]);
    const sKey = String(s);
    const tKey = String(t);
    const key = sKey < tKey ? `${sKey}|${tKey}` : `${tKey}|${sKey}`;
    const forward = sKey < tKey;
    if (!pairOrder.has(key)) pairOrder.set(key, { indices: [], forward: [] });
    const group = pairOrder.get(key)!;
    group.indices.push(i);
    group.forward.push(forward);
  }
  const result = new Map<number, MultiEdgeInfo>();
  for (const group of pairOrder.values()) {
    for (let j = 0; j < group.indices.length; j++) {
      result.set(group.indices[j], {
        count: group.indices.length,
        indexInGroup: j,
      });
    }
  }
  return result;
}

export function getControlPoint(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  offset: number,
): { cx: number; cy: number } {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  return { cx: mx + nx * offset, cy: my + ny * offset };
}

export function drawArrowhead(
  ctx: CanvasRenderingContext2D,
  tipX: number,
  tipY: number,
  angle: number,
  size: number,
) {
  const halfAngle = Math.PI / 7;
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(
    tipX - size * Math.cos(angle - halfAngle),
    tipY - size * Math.sin(angle - halfAngle),
  );
  ctx.lineTo(
    tipX - size * Math.cos(angle + halfAngle),
    tipY - size * Math.sin(angle + halfAngle),
  );
  ctx.closePath();
  ctx.fill();
}

export function shortenEdge(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  shortenEnd: number,
  shortenStart: number,
): { sx: number; sy: number; ex: number; ey: number } {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  return {
    sx: x1 + ux * shortenStart,
    sy: y1 + uy * shortenStart,
    ex: x2 - ux * shortenEnd,
    ey: y2 - uy * shortenEnd,
  };
}

export function drawEdgeCurve(
  ctx: CanvasRenderingContext2D,
  sx: number,
  sy: number,
  ex: number,
  ey: number,
  cx: number,
  cy: number,
  isCurved: boolean,
) {
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  if (isCurved) {
    ctx.quadraticCurveTo(cx, cy, ex, ey);
  } else {
    ctx.lineTo(ex, ey);
  }
  ctx.stroke();
}

export function getCurveMidpoint(
  x1: number,
  y1: number,
  cx: number,
  cy: number,
  x2: number,
  y2: number,
): { mx: number; my: number } {
  const t = 0.5;
  const mt = 1 - t;
  return {
    mx: mt * mt * x1 + 2 * mt * t * cx + t * t * x2,
    my: mt * mt * y1 + 2 * mt * t * cy + t * t * y2,
  };
}

export function computeCurveOffset(
  indexInGroup: number,
  groupSize: number,
  spacing: number,
): number {
  if (groupSize <= 1) return 0;
  return (indexInGroup - (groupSize - 1) / 2) * spacing;
}

export function getArrowAngle(
  cx: number,
  cy: number,
  ex: number,
  ey: number,
): number {
  return Math.atan2(ey - cy, ex - cx);
}
