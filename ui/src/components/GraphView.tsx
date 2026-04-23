import { useCallback, useEffect, useRef, useState } from "react";
import { getVaultGraph, getVaultEntitySources, type GraphData, type GraphNode, type EntityNode } from "../api";
import VaultFilePreview from "./VaultFilePreview";
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
import "./GraphView.css";

const PALETTE = [
  "#b87333", "#7a9e7e", "#c9a84c", "#9e4a3a",
  "#5e7a9e", "#7a5e9e", "#9e7a5e", "#4a9e7a",
];

const TYPE_COLORS: Record<string, string> = {
  person: "#c9a84c", project: "#b87333", concept: "#7a5e9e",
  technology: "#5e7a9e", decision: "#9e4a3a", resource: "#4a9e7a",
};

function folderColor(folder: string): string {
  if (!folder) return PALETTE[0];
  let h = 0;
  for (let i = 0; i < folder.length; i++) h = (h * 31 + folder.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function entityColor(type: string): string {
  return TYPE_COLORS[type] ?? "#7a9e7e";
}

function nodeRadius(size: number): number {
  const r = Math.log(Math.max(size, 1) + 1) * 1.8;
  return Math.max(4, Math.min(14, r));
}

type NodeType = "file" | "entity";

interface SimNode {
  id: string;
  nodeType: NodeType;
  x: number; y: number; vx: number; vy: number;
  pinned: boolean;
  data: GraphNode | null;
  entity: EntityNode | null;
}

interface EdgeTypeConfig {
  dash: number[];
  color: string;
  alpha: number;
}

const EDGE_STYLES: Record<string, EdgeTypeConfig> = {
  link: { dash: [], color: "", alpha: 0.5 },
  "tag-cooccurrence": { dash: [4, 4], color: "#c9a84c", alpha: 0.35 },
  "shared-entity": { dash: [6, 3], color: "#7a5e9e", alpha: 0.4 },
  "folder-cross": { dash: [2, 4], color: "#5e7a9e", alpha: 0.25 },
};

const REPULSION_K = 3000;
const SPRING_K    = 0.03;
const REST_LEN    = 80;
const GRAVITY     = 0.01;
const DAMPING     = 0.88;
const ENERGY_STOP = 0.15;

type ScopeType = "all" | "file" | "folder" | "tag" | "search" | "entity";

interface DetailInfo {
  type: "file" | "entity";
  path?: string;
  entity?: EntityNode;
}

export default function GraphView({ onViewEntityGraph }: { onViewEntityGraph?: (path: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);

  const [scope, setScope] = useState<ScopeType>("all");
  const [seed, setSeed] = useState("");
  const [hops, setHops] = useState(1);
  const [edgeTypes, setEdgeTypes] = useState("link");
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set());
  const [showFilters, setShowFilters] = useState(false);
  const [detail, setDetail] = useState<DetailInfo | null>(null);
  const [detailEntities, setDetailEntities] = useState<{ id: number; name: string; type: string }[]>([]);
  const [loading, setLoading] = useState(false);

  const nodesRef   = useRef<SimNode[]>([]);
  const graphRef   = useRef<GraphData | null>(null);
  const rafRef     = useRef<number | null>(null);
  const runningRef = useRef(false);
  const offsetRef  = useRef({ x: 0, y: 0 });
  const scaleRef   = useRef(1);
  const dragRef    = useRef<{ nodeIdx: number | null; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef     = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);
  const hoverRef   = useRef<number | null>(null);
  const settledRef = useRef(false);
  const selectedRef = useRef<number | null>(null);
  const [_renderTick, setRenderTick] = useState(0);

  const fetchGraph = useCallback(() => {
    setError(null);
    setLoading(true);
    const params = scope !== "all" && seed
      ? { scope, seed, hops, edge_types: edgeTypes }
      : { edge_types: edgeTypes };
    getVaultGraph(params)
      .then((g) => {
        setGraph(g);
        graphRef.current = g;
        setDetail(null);
        initSim(g, canvasRef.current);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load graph");
      })
      .finally(() => setLoading(false));
  }, [scope, seed, hops, edgeTypes]);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

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

  function startRAF() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  }

  const allTags = (() => {
    const tags = new Set<string>();
    graph?.nodes.forEach(n => n.tags?.forEach(t => tags.add(t)));
    return Array.from(tags).sort();
  })();

  function filteredGraph(): GraphData | null {
    const g = graphRef.current;
    if (!g) return null;
    if (tagFilter.size === 0) return g;

    const visiblePaths = new Set(
      g.nodes.filter(n => n.tags?.some(t => tagFilter.has(t))).map(n => n.path)
    );
    if (visiblePaths.size === 0) return g;

    const filteredNodes = g.nodes.filter(n => visiblePaths.has(n.path));
    const filteredEdges = g.edges.filter(e => visiblePaths.has(e.from) && visiblePaths.has(e.to));
    const connected = new Set<string>();
    for (const e of filteredEdges) { connected.add(e.from); connected.add(e.to); }
    return {
      ...g,
      nodes: filteredNodes,
      edges: filteredEdges,
      orphans: filteredNodes.filter(n => !connected.has(n.path)).map(n => n.path),
    };
  }

  function tick() {
    if (!runningRef.current) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const g = filteredGraph();
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

    for (let i = 0; i < nodes.length; i++) {
      fx[i] += (cx - nodes[i].x) * GRAVITY;
      fy[i] += (cy - nodes[i].y) * GRAVITY;
    }

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

  function draw(canvas: HTMLCanvasElement, g: GraphData, nodes: SimNode[]) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const hover = hoverRef.current;
    const selected = selectedRef.current;
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

      if (highlighted && settledRef.current && edgeType !== "link") {
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
        if (settledRef.current) {
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

        if (settledRef.current) {
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

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const ro = new ResizeObserver(() => {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      const g = filteredGraph();
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvas, g, nodes);
    });
    ro.observe(parent);
    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;
    return () => ro.disconnect();
  }, []);

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
      let r: number;
      if (n.nodeType === "entity") {
        r = 8;
      } else if (n.data) {
        r = nodeRadius(n.data.size) + 4;
      } else continue;
      const dx = cx - n.x; const dy = cy - n.y;
      if (dx * dx + dy * dy <= r * r) return i;
    }
    return null;
  }

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
      const g = filteredGraph();
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvasRef.current!, g, nodes);
    } else {
      const g = filteredGraph();
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvasRef.current!, g, nodes);
    }
  }

  function onMouseUp(e: React.MouseEvent) {
    if (dragRef.current && !dragRef.current.moved) {
      const { x, y } = canvasPoint(e);
      const hit = hitTest(x, y);
      if (hit !== null) {
        selectedRef.current = hit;
        const n = nodesRef.current[hit];
        if (n.nodeType === "file" && n.data) {
          setDetail({ type: "file", path: n.data.path });
          setPreviewPath(n.data.path);
          getVaultEntitySources(n.data.path).then(r => setDetailEntities(r.entities ?? [])).catch(() => setDetailEntities([]));
        } else if (n.nodeType === "entity" && n.entity) {
          setDetail({ type: "entity", entity: n.entity });
          setPreviewPath(null);
          setDetailEntities([]);
        }
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

    const g = filteredGraph();
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
    const g = filteredGraph();
    if (g) draw(canvas, g, nodes);
  }

  function exploreFrom(path: string) {
    setScope("file");
    setSeed(path);
    setHops(1);
  }

  function exploreEntity(entityId: number) {
    setScope("entity");
    setSeed(String(entityId));
    setHops(1);
  }

  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;
  const entityCount = graph?.entity_nodes?.length ?? 0;

  const scopeLabels: Record<ScopeType, string> = {
    all: "All", file: "File", folder: "Folder", tag: "Tag", search: "Search", entity: "Entity",
  };
  const seedPlaceholders: Record<ScopeType, string> = {
    all: "", file: "path/to/file.md", folder: "folder/", tag: "tag-name", search: "search query…", entity: "entity ID",
  };

  return (
    <div className="graph-view">
      <div className="graph-toolbar">
        <select
          className="graph-toolbar-select"
          value={scope}
          onChange={e => { setScope(e.target.value as ScopeType); setSeed(""); }}
        >
          {Object.entries(scopeLabels).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>

        {scope !== "all" && (
          <input
            className="graph-toolbar-input"
            type="text"
            placeholder={seedPlaceholders[scope]}
            value={seed}
            onChange={e => setSeed(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") fetchGraph(); }}
          />
        )}

        <select className="graph-toolbar-select graph-toolbar-select-sm" value={hops} onChange={e => setHops(Number(e.target.value))}>
          <option value={1}>1 hop</option>
          <option value={2}>2 hops</option>
          <option value={3}>3 hops</option>
        </select>

        <select
          className="graph-toolbar-select graph-toolbar-select-sm"
          value={edgeTypes}
          onChange={e => setEdgeTypes(e.target.value)}
        >
          <option value="link">Links</option>
          <option value="link,tag">Links + Tags</option>
          <option value="link,entity">Links + Entities</option>
          <option value="link,tag,entity">All</option>
        </select>

        <button className="graph-toolbar-btn" onClick={() => setShowFilters(f => !f)}>
          Tags
        </button>

        <button className="graph-toolbar-btn" onClick={fitToView}>Fit</button>
        <button className="graph-toolbar-btn" onClick={fetchGraph} disabled={loading}>
          {loading ? "…" : "Go"}
        </button>
        <span className="graph-toolbar-stat">{nodeCount} nodes</span>
        <span className="graph-toolbar-stat">{edgeCount} edges</span>
        {entityCount > 0 && <span className="graph-toolbar-stat">{entityCount} entities</span>}
      </div>

      {showFilters && allTags.length > 0 && (
        <div className="graph-filter-bar">
          {allTags.map(tag => (
            <button
              key={tag}
              className={`graph-tag-chip${tagFilter.has(tag) ? " active" : ""}`}
              onClick={() => {
                const next = new Set(tagFilter);
                if (next.has(tag)) next.delete(tag); else next.add(tag);
                setTagFilter(next);
                setRenderTick(t => t + 1);
              }}
            >
              {tag}
            </button>
          ))}
          {tagFilter.size > 0 && (
            <button className="graph-tag-chip" onClick={() => { setTagFilter(new Set()); setRenderTick(t => t + 1); }}>
              clear
            </button>
          )}
        </div>
      )}

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

      {detail && (
        <div className="graph-detail-panel">
          <div className="graph-detail-header">
            <span className="graph-detail-title">
              {detail.type === "file"
                ? (graph?.nodes.find(n => n.path === detail.path)?.title || detail.path?.split("/").pop() || "")
                : detail.entity?.name || ""}
            </span>
            <button className="graph-detail-close" onClick={() => { setDetail(null); setPreviewPath(null); selectedRef.current = null; }}>&times;</button>
          </div>
          <div className="graph-detail-body">
            {detail.type === "file" && detail.path && (
              <>
                <div className="graph-detail-meta">
                  <span className="graph-detail-label">Path</span>
                  <span className="graph-detail-value">{detail.path}</span>
                </div>
                {(() => {
                  const node = graph?.nodes.find(n => n.path === detail.path);
                  return node?.tags?.length ? (
                    <div className="graph-detail-meta">
                      <span className="graph-detail-label">Tags</span>
                      <div className="graph-detail-tags">
                        {node.tags.map(t => (
                          <span key={t} className="graph-detail-tag" onClick={() => { setScope("tag"); setSeed(t); }}>{t}</span>
                        ))}
                      </div>
                    </div>
                  ) : null;
                })()}
                <button className="graph-detail-action" onClick={() => exploreFrom(detail.path!)}>
                  Explore from here
                </button>
                {detailEntities.length > 0 && (
                  <div className="graph-detail-section">
                    <span className="graph-detail-label">Entities ({detailEntities.length})</span>
                    <div className="graph-detail-entities">
                      {detailEntities.map(en => (
                        <span key={en.id} className="graph-detail-entity" onClick={() => exploreEntity(en.id)}>
                          <span className="graph-detail-entity-dot" style={{ background: entityColor(en.type) }} />
                          {en.name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
            {detail.type === "entity" && detail.entity && (
              <>
                <div className="graph-detail-meta">
                  <span className="graph-detail-label">Type</span>
                  <span className="graph-detail-value">{detail.entity.type}</span>
                </div>
                <button className="graph-detail-action" onClick={() => exploreEntity(detail.entity!.id)}>
                  Explore from here
                </button>
                {detail.entity.source_paths.length > 0 && (
                  <div className="graph-detail-section">
                    <span className="graph-detail-label">Source files ({detail.entity.source_paths.length})</span>
                    <div className="graph-detail-sources">
                      {detail.entity.source_paths.map(sp => (
                        <span key={sp} className="graph-detail-source" onClick={() => { setPreviewPath(sp); }}>
                          {sp}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {previewPath && !detail && (
        <VaultFilePreview
          path={previewPath}
          onClose={() => setPreviewPath(null)}
          onViewEntityGraph={onViewEntityGraph}
        />
      )}
    </div>
  );
}
