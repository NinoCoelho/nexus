/**
 * AgentGraphView — agent/skill/session graph on a Cytoscape canvas.
 *
 * Shows the Nexus agent node, all registered skills, and recent sessions.
 * Edges connect sessions to the skills they used during execution.
 *
 * Interactions:
 *   - Skill node click → opens the SkillDrawer
 *   - Session node click → navigates to that chat session
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getAgentGraph, type AgentGraphData, type AgentGraphNode } from "../api";
import {
  buildMultiEdgeIndex,
  computeCurveOffset,
  getControlPoint,
  getCurveMidpoint,
  getArrowAngle,
  drawArrowhead,
  shortenEdge,
  drawEdgeCurve,
} from "./graphEdgeUtils";
import "./AgentGraphView.css";

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
}

// ── node sizing / colour by type ──────────────────────────────────────────────
function nodeRadius(type: AgentGraphNode["type"]): number {
  if (type === "agent") return 14;
  if (type === "skill") return 8;
  return 5; // session
}

// ── sim node ──────────────────────────────────────────────────────────────────
interface SimNode extends AgentGraphNode {
  x: number; y: number; vx: number; vy: number;
  pinned: boolean;
}

// ── physics ───────────────────────────────────────────────────────────────────
const REPULSION_K = 3000;
const SPRING_K    = 0.03;
const REST_LEN    = 90;
const GRAVITY     = 0.01;
const DAMPING     = 0.88;
const ENERGY_STOP = 0.15;

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}

export default function AgentGraphView({ onOpenSkill, onSelectSession }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [graph, setGraph] = useState<AgentGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const nodesRef = useRef<SimNode[]>([]);
  const graphRef = useRef<AgentGraphData | null>(null);
  const rafRef = useRef<number | null>(null);
  const runningRef = useRef(false);

  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef(1);

  const dragRef = useRef<{ nodeIdx: number | null; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);

  const hoverRef = useRef<number | null>(null);
  const settledRef = useRef(false);

  // ── fetch ───────────────────────────────────────────────────────────────────
  const fetchGraph = useCallback(() => {
    setError(null);
    getAgentGraph()
      .then((g) => {
        setGraph(g);
        graphRef.current = g;
        initSim(g, canvasRef.current);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load graph");
      });
  }, []);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  // ── init sim ────────────────────────────────────────────────────────────────
  function initSim(g: AgentGraphData, canvas: HTMLCanvasElement | null) {
    const w = canvas?.width ?? 800;
    const h = canvas?.height ?? 600;
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

  // ── RAF ─────────────────────────────────────────────────────────────────────
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

  // ── draw ────────────────────────────────────────────────────────────────────
  function draw(canvas: HTMLCanvasElement, g: AgentGraphData, nodes: SimNode[]) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const hover = hoverRef.current;

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

      if (highlighted && settledRef.current && e.label) {
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
    if (settledRef.current) {
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

  // ── canvas sizing ───────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const ro = new ResizeObserver(() => {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      const g = graphRef.current;
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvas, g, nodes);
    });
    ro.observe(parent);

    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;

    return () => ro.disconnect();
  }, []);

  // ── hit testing ─────────────────────────────────────────────────────────────
  function canvasPoint(e: React.MouseEvent): { x: number; y: number } {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const sc = scaleRef.current;
    const { x: ox, y: oy } = offsetRef.current;
    return { x: (mx - ox) / sc, y: (my - oy) / sc };
  }

  function hitTest(cx: number, cy: number): number | null {
    const nodes = nodesRef.current;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = nodeRadius(n.type) + 4;
      const dx = cx - n.x; const dy = cy - n.y;
      if (dx * dx + dy * dy <= r * r) return i;
    }
    return null;
  }

  // ── mouse ───────────────────────────────────────────────────────────────────
  function onMouseDown(e: React.MouseEvent) {
    const { x, y } = canvasPoint(e);
    const hit = hitTest(x, y);
    if (hit !== null) {
      dragRef.current = { nodeIdx: hit, startX: e.clientX, startY: e.clientY, moved: false };
    } else {
      const { x: ox, y: oy } = offsetRef.current;
      panRef.current = { ox, oy, mx: e.clientX, my: e.clientY };
    }
  }

  function onMouseMove(e: React.MouseEvent) {
    const { x: cx, y: cy } = canvasPoint(e);
    const hit = hitTest(cx, cy);
    hoverRef.current = hit;

    if (dragRef.current?.nodeIdx !== null && dragRef.current !== null) {
      dragRef.current.moved = true;
      const n = nodesRef.current[dragRef.current.nodeIdx!];
      n.x = cx; n.y = cy;
      n.vx = 0; n.vy = 0;
      n.pinned = true;
      if (!runningRef.current) {
        runningRef.current = true;
        settledRef.current = false;
        startRAF();
      }
    } else if (panRef.current) {
      offsetRef.current = {
        x: panRef.current.ox + e.clientX - panRef.current.mx,
        y: panRef.current.oy + e.clientY - panRef.current.my,
      };
      const g = graphRef.current;
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvasRef.current!, g, nodes);
    } else {
      const g = graphRef.current;
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvasRef.current!, g, nodes);
    }
  }

  function onMouseUp(e: React.MouseEvent) {
    if (dragRef.current && !dragRef.current.moved) {
      const { x, y } = canvasPoint(e);
      const hit = hitTest(x, y);
      if (hit !== null) {
        const n = nodesRef.current[hit];
        if (n.type === "skill") onOpenSkill(n.id.replace(/^skill:/, ""));
        else if (n.type === "session") onSelectSession(n.id.replace(/^session:/, ""));
      }
    }
    dragRef.current = null;
    panRef.current = null;
  }

  function onDoubleClick() {
    const g = graphRef.current;
    const canvas = canvasRef.current;
    if (g && canvas) initSim(g, canvas);
  }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    offsetRef.current = {
      x: mx - (mx - ox) * (newSc / sc),
      y: my - (my - oy) * (newSc / sc),
    };
    scaleRef.current = newSc;

    const g = graphRef.current;
    const nodes = nodesRef.current;
    if (g && nodes.length > 0) draw(canvas, g, nodes);
  }

  function fitToView() {
    const canvas = canvasRef.current;
    const nodes = nodesRef.current;
    if (!canvas || nodes.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
    }
    const pad = 40;
    const bw = maxX - minX + pad * 2;
    const bh = maxY - minY + pad * 2;
    const sc = Math.min(canvas.width / bw, canvas.height / bh, 2);
    scaleRef.current = sc;
    offsetRef.current = {
      x: (canvas.width - bw * sc) / 2 + (pad - minX) * sc,
      y: (canvas.height - bh * sc) / 2 + (pad - minY) * sc,
    };

    const g = graphRef.current;
    if (g) draw(canvas, g, nodes);
  }

  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;

  return (
    <div className="agent-graph-view">
      <div className="agent-graph-toolbar">
        <button className="agent-graph-toolbar-btn" onClick={fitToView}>Fit to view</button>
        <button className="agent-graph-toolbar-btn" onClick={fetchGraph}>Refresh</button>
        <span className="agent-graph-toolbar-stat">{nodeCount} nodes</span>
        <span className="agent-graph-toolbar-stat">{edgeCount} edges</span>
      </div>

      {error && <div className="agent-graph-error">{error}</div>}

      <canvas
        ref={canvasRef}
        className="agent-graph-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onDoubleClick={onDoubleClick}
        onWheel={onWheel}
      />
    </div>
  );
}
