import { useCallback, useEffect, useRef, useState } from "react";
import { getVaultGraph, type GraphData, type GraphNode } from "../api";
import VaultFilePreview from "./VaultFilePreview";
import "./GraphView.css";

// ── colour palette ─────────────────────────────────────────────────────────────
const PALETTE = [
  "#b87333", // copper
  "#7a9e7e", // sage
  "#c9a84c", // amber
  "#9e4a3a", // rust
  "#5e7a9e", // steel blue
  "#7a5e9e", // violet
  "#9e7a5e", // tan
  "#4a9e7a", // teal
];

function folderColor(folder: string): string {
  if (!folder) return PALETTE[0];
  let h = 0;
  for (let i = 0; i < folder.length; i++) h = (h * 31 + folder.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// ── node radius ────────────────────────────────────────────────────────────────
function nodeRadius(size: number): number {
  const r = Math.log(Math.max(size, 1) + 1) * 1.8;
  return Math.max(4, Math.min(14, r));
}

// ── sim node ──────────────────────────────────────────────────────────────────
interface SimNode extends GraphNode {
  x: number; y: number; vx: number; vy: number;
  pinned: boolean;
}

// ── constants ─────────────────────────────────────────────────────────────────
const REPULSION_K = 3000;
const SPRING_K    = 0.03;
const REST_LEN    = 80;
const GRAVITY     = 0.01;
const DAMPING     = 0.88;
const ENERGY_STOP = 0.15;

export default function GraphView() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);

  // sim state stored in refs so the RAF loop sees latest values
  const nodesRef   = useRef<SimNode[]>([]);
  const graphRef   = useRef<GraphData | null>(null);
  const rafRef     = useRef<number | null>(null);
  const runningRef = useRef(false);

  // pan/zoom
  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef  = useRef(1);

  // drag state
  const dragRef = useRef<{ nodeIdx: number | null; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef  = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);

  // hover
  const hoverRef = useRef<number | null>(null);

  // label visibility: hide while sim is running
  const settledRef = useRef(false);

  // ── fetch ─────────────────────────────────────────────────────────────────
  const fetchGraph = useCallback(() => {
    setError(null);
    getVaultGraph()
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

  // ── init sim ──────────────────────────────────────────────────────────────
  function initSim(g: GraphData, canvas: HTMLCanvasElement | null) {
    const w = canvas?.width ?? 800;
    const h = canvas?.height ?? 600;
    nodesRef.current = g.nodes.map((n) => ({
      ...n,
      x: w * 0.2 + Math.random() * w * 0.6,
      y: h * 0.2 + Math.random() * h * 0.6,
      vx: 0, vy: 0,
      pinned: false,
    }));
    settledRef.current = false;
    runningRef.current = true;
    startRAF();
  }

  // ── RAF loop ──────────────────────────────────────────────────────────────
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

    // build index map for O(1) edge lookup
    const idx = new Map<string, number>();
    nodes.forEach((n, i) => idx.set(n.path, i));

    // forces
    const fx = new Float64Array(nodes.length);
    const fy = new Float64Array(nodes.length);

    // repulsion O(n²)
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

    // spring attraction along edges
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

    // gravity toward center
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
      draw(canvas, g, nodes); // final draw with labels
    }
  }

  // ── draw ──────────────────────────────────────────────────────────────────
  function draw(canvas: HTMLCanvasElement, g: GraphData, nodes: SimNode[]) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const hover = hoverRef.current;
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
    nodes.forEach((n, i) => idx.set(n.path, i));

    // edges
    for (const e of g.edges) {
      const ai = idx.get(e.from); const bi = idx.get(e.to);
      if (ai === undefined || bi === undefined) continue;
      const highlighted = hover === ai || hover === bi;
      ctx.beginPath();
      ctx.moveTo(nodes[ai].x, nodes[ai].y);
      ctx.lineTo(nodes[bi].x, nodes[bi].y);
      ctx.strokeStyle = highlighted ? accent : borderColor;
      ctx.lineWidth = highlighted ? 1.5 : 0.8;
      ctx.globalAlpha = highlighted ? 1 : 0.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // nodes
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const r = nodeRadius(n.size);
      const color = folderColor(n.folder);
      const isOrphan = orphanSet.has(n.path);
      const isHover = hover === i;

      ctx.beginPath();
      ctx.arc(n.x, n.y, r + (isHover ? 2 : 0), 0, Math.PI * 2);

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
        if (isHover) {
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
      }

      // label — only when settled
      if (settledRef.current) {
        const basename = n.path.split("/").pop() ?? n.path;
        const label = basename.replace(/\.mdx?$/, "");
        ctx.font = "10px system-ui, sans-serif";
        ctx.fillStyle = fgDim;
        ctx.textAlign = "center";
        ctx.fillText(label, n.x, n.y + r + 12);
      }
    }

    ctx.restore();
  }

  // ── canvas sizing ─────────────────────────────────────────────────────────
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

    // initial size
    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;

    return () => ro.disconnect();
  }, []);

  // ── mouse helpers ─────────────────────────────────────────────────────────
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
      const r = nodeRadius(n.size) + 4;
      const dx = cx - n.x; const dy = cy - n.y;
      if (dx * dx + dy * dy <= r * r) return i;
    }
    return null;
  }

  // ── mouse events ──────────────────────────────────────────────────────────
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
      // just hover redraw
      const g = graphRef.current;
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvasRef.current!, g, nodes);
    }
  }

  function onMouseUp(e: React.MouseEvent) {
    if (dragRef.current && !dragRef.current.moved) {
      // click — open preview
      const { x, y } = canvasPoint(e);
      const hit = hitTest(x, y);
      if (hit !== null) {
        setPreviewPath(nodesRef.current[hit].path);
      }
    }
    dragRef.current = null;
    panRef.current = null;
  }

  function onDoubleClick() {
    const g = graphRef.current;
    const canvas = canvasRef.current;
    if (g && canvas) {
      initSim(g, canvas);
    }
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

  // ── fit to view ───────────────────────────────────────────────────────────
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

  // cleanup
  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;

  return (
    <div className="graph-view">
      <div className="graph-toolbar">
        <button className="graph-toolbar-btn" onClick={fitToView}>Fit to view</button>
        <button className="graph-toolbar-btn" onClick={fetchGraph}>Refresh</button>
        <span className="graph-toolbar-stat">{nodeCount} nodes</span>
        <span className="graph-toolbar-stat">{edgeCount} edges</span>
      </div>

      {error && <div className="graph-error">{error}</div>}

      <canvas
        ref={canvasRef}
        className="graph-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onDoubleClick={onDoubleClick}
        onWheel={onWheel}
      />

      {previewPath && (
        <VaultFilePreview
          path={previewPath}
          onClose={() => setPreviewPath(null)}
        />
      )}
    </div>
  );
}
