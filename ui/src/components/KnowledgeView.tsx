/**
 * KnowledgeView — GraphRAG knowledge graph explorer.
 *
 * Three panels:
 *   1. Entity browser — paginated list of extracted entities with type filter
 *   2. Subgraph visualizer — Cytoscape canvas showing entity/relation graph
 *   3. Query interface — natural language search over the knowledge graph
 *
 * The view supports:
 *   - Filtering the graph to a specific file/folder (via graphSourceFilter)
 *   - Reindexing the GraphRAG engine (incremental or full)
 *   - Clicking entities/relations for detail views
 *   - Drilling down into entity subgraphs by hop count
 *
 * All data comes from the /graph/knowledge/* API endpoints backed by
 * nexus.agent.graphrag_manager and loom.store.graphrag.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  knowledgeQuery,
  getKnowledgeStats,
  getKnowledgeEntities,
  getKnowledgeEntity,
  getKnowledgeSubgraph,
  getKnowledgeFileSubgraph,
  getKnowledgeFolderSubgraph,
  getVaultTree,
  type KnowledgeStats,
  type KnowledgeEntity,
  type KnowledgeQueryResult,
  type EntityDetail,
  type SubgraphData,
} from "../api";
import VaultFilePreview from "./VaultFilePreview";
import { shortenEdge } from "./graphEdgeUtils";
import "./KnowledgeView.css";

const TYPE_COLORS: Record<string, string> = {
  person: "#c9a84c",
  project: "#b87333",
  concept: "#7a5e9e",
  technology: "#5e7a9e",
  decision: "#9e4a3a",
  resource: "#4a9e7a",
};
const DEFAULT_COLOR = "#7a9e7e";

function typeColor(t: string) {
  return TYPE_COLORS[t] ?? DEFAULT_COLOR;
}

function nodeRadius(degree: number) {
  return Math.max(3, Math.min(10, 3 + Math.log(degree + 1) * 1.8));
}

function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

interface SimNode {
  id: number; name: string; type: string; degree: number;
  x: number; y: number; vx: number; vy: number; pinned: boolean;
}

interface MergedEdgeGroup {
  nodeA: number; // lower id
  nodeB: number; // higher id
  relations: Array<{ label: string; from: number; to: number }>;
}

function buildMergedEdges(edges: Array<{ source: number | string; target: number | string; relation?: string }>): MergedEdgeGroup[] {
  const groups = new Map<string, MergedEdgeGroup>();
  for (const e of edges) {
    const s = Number(e.source);
    const t = Number(e.target);
    const lo = Math.min(s, t);
    const hi = Math.max(s, t);
    const key = `${lo}|${hi}`;
    if (!groups.has(key)) {
      groups.set(key, { nodeA: lo, nodeB: hi, relations: [] });
    }
    groups.get(key)!.relations.push({
      label: e.relation || "",
      from: s,
      to: t,
    });
  }
  return Array.from(groups.values());
}

function EntityDetailCard({
  detail,
  pinned,
  onPin,
  onUnpin,
  onClose,
  onSelectEntity,
  onPreview,
}: {
  detail: EntityDetail;
  pinned: boolean;
  onPin: () => void;
  onUnpin: () => void;
  onClose: () => void;
  onSelectEntity: (id: number) => void;
  onPreview: (path: string) => void;
}) {
  if (!detail.entity) return null;
  return (
    <div className={`kv-entity-detail${pinned ? " kv-entity-detail--pinned" : ""}`}>
      <div className="kv-entity-detail-header">
        <span className="kv-entity-dot" style={{ background: typeColor(detail.entity.type) }} />
        <h3 className="kv-entity-detail-name">{detail.entity.name}</h3>
        <span className="kv-entity-detail-type">{detail.entity.type}</span>
        <button
          className={`kv-entity-detail-pin${pinned ? " kv-entity-detail-pin--active" : ""}`}
          onClick={pinned ? onUnpin : onPin}
          title={pinned ? "Unpin" : "Pin"}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9.828.722a.5.5 0 0 1 .354.146l4.95 4.95a.5.5 0 0 1-.354.853H11.5l-2.5 5-1.5-1.5-3.354 3.354a.5.5 0 0 1-.707-.708L6.793 9.47l-1.5-1.5 5-2.5V1.222a.5.5 0 0 1 .535-.5Z" />
          </svg>
        </button>
        <button className="kv-entity-detail-close" onClick={onClose}>&times;</button>
      </div>
      <div className="kv-entity-detail-degree">{detail.degree} connections</div>

      {detail.relations.length > 0 && (
        <div className="kv-entity-relations">
          <h4>Relations</h4>
          {detail.relations.slice(0, 20).map((rel, i) => (
            <button
              key={i}
              className="kv-relation-row"
              onClick={() => onSelectEntity(rel.entity_id)}
            >
              <span className="kv-relation-dir">{rel.direction === "outgoing" ? "\u2192" : "\u2190"}</span>
              <span className="kv-relation-name">{rel.relation.replace(/_/g, " ")}</span>
              <span className="kv-relation-entity">
                <span className="kv-entity-dot kv-entity-dot--sm" style={{ background: typeColor(rel.entity_type) }} />
                {rel.entity_name}
              </span>
            </button>
          ))}
        </div>
      )}

      {detail.chunks.length > 0 && (
        <div className="kv-entity-chunks">
          <h4>Source Documents</h4>
          {detail.chunks.map((c) => (
            <button
              key={c.chunk_id}
              className="kv-chunk-row"
              onClick={() => onPreview(c.source_path)}
            >
              {c.source_path} &rsaquo; {c.heading}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function KnowledgeView({
  initialSourceFilter,
  onSourceFilterHandled,
  onViewEntityGraph,
  onStartGraphIndex,
}: {
  initialSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onSourceFilterHandled?: () => void;
  onViewEntityGraph?: (path: string) => void;
  onStartGraphIndex?: (path: string) => void;
}) {
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [queryResult, setQueryResult] = useState<KnowledgeQueryResult | null>(null);
  const [topEntities, setTopEntities] = useState<KnowledgeEntity[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<EntityDetail | null>(null);
  const [pinnedEntities, setPinnedEntities] = useState<EntityDetail[]>([]);
  const [subgraphData, setSubgraphData] = useState<SubgraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [entityFilter, setEntityFilter] = useState("");
  const [splitRatio, setSplitRatio] = useState(0.5);
  const [sourceFilter, setSourceFilter] = useState<"none" | "file" | "folder">("none");
  const [sourcePath, setSourcePath] = useState("");
  const [sourceSuggestions, setSourceSuggestions] = useState<string[]>([]);
  const [showSourceSuggestions, setShowSourceSuggestions] = useState(false);
  const [graphSearch, setGraphSearch] = useState("");
  const [graphSearchCount, setGraphSearchCount] = useState(0);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simNodesRef = useRef<SimNode[]>([]);
  const subgraphRef = useRef<SubgraphData | null>(null);
  const mergedEdgesRef = useRef<MergedEdgeGroup[]>([]);
  const rafRef = useRef<number | null>(null);
  const runningRef = useRef(false);
  const tickRef = useRef(0);
  const settledRef = useRef(false);
  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef(1);
  const hoverRef = useRef<number | null>(null);
  const selectedNodeRef = useRef<number | null>(null);
  const selectedEdgeRef = useRef<number | null>(null);
  const hoveredEdgeGroupRef = useRef<number | null>(null);
  const dragRef = useRef<{ idx: number; moved: boolean } | null>(null);
  const panRef = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const splitDragRef = useRef<{ startX: number; startRatio: number } | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);
  const edgeTooltipRef = useRef<HTMLDivElement | null>(null);
  const highlightNodesRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    getKnowledgeStats().then(setStats).catch(() => {});
    getKnowledgeEntities({ limit: 200 }).then((r) => setTopEntities(r.entities)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!initialSourceFilter) return;
    const { mode, path } = initialSourceFilter;
    setSourceFilter(mode);
    setSourcePath(path);
    void applySourceFilter(mode, path);
    onSourceFilterHandled?.();
  }, [initialSourceFilter]);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) return;
    setLoading(true);
    setSelectedEntity(null);
    setPinnedEntities([]);
    selectedNodeRef.current = null;
    selectedEdgeRef.current = null;
    try {
      const result = await knowledgeQuery(q);
      setQueryResult(result);
      if (result.subgraph.nodes.length > 0) {
        setSubgraphData({
          enabled: true,
          nodes: result.subgraph.nodes.map((n) => ({ ...n, degree: n.degree ?? 0 })),
          edges: result.subgraph.edges,
        });
      }
    } catch {
      setQueryResult(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clearSearch = useCallback(() => {
    setQueryText("");
    setQueryResult(null);
    setSubgraphData(null);
    setSelectedEntity(null);
    setPinnedEntities([]);
    selectedNodeRef.current = null;
    selectedEdgeRef.current = null;
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const applySourceFilter = useCallback(async (mode: "file" | "folder", path: string) => {
    if (!path.trim()) return;
    setLoading(true);
    setSelectedEntity(null);
    setPinnedEntities([]);
    selectedNodeRef.current = null;
    selectedEdgeRef.current = null;
    try {
      const sg = mode === "file"
        ? await getKnowledgeFileSubgraph(path)
        : await getKnowledgeFolderSubgraph(path);
      setSubgraphData(sg);
      setQueryResult(null);
    } catch {
      setSubgraphData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clearSourceFilter = useCallback(() => {
    setSourceFilter("none");
    setSourcePath("");
    setSubgraphData(null);
    setShowSourceSuggestions(false);
  }, []);

  const onSearchChange = useCallback((value: string) => {
    setQueryText(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!value.trim()) {
      setQueryResult(null);
      setSubgraphData(null);
      return;
    }
    debounceRef.current = setTimeout(() => void doSearch(value), 300);
  }, [doSearch]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const selectEntity = useCallback(async (id: number) => {
    try {
      const [detail, sg] = await Promise.all([
        getKnowledgeEntity(id),
        getKnowledgeSubgraph(id, 2),
      ]);
      setSelectedEntity(detail);
      setSubgraphData(sg);
      selectedNodeRef.current = null;
      selectedEdgeRef.current = null;
    } catch {
      setSelectedEntity(null);
    }
  }, []);

  const pinEntity = useCallback((detail: EntityDetail) => {
    if (!detail.entity) return;
    setPinnedEntities((prev) => {
      if (prev.some((p) => p.entity?.id === detail.entity!.id)) return prev;
      return [...prev, detail];
    });
  }, []);

  const unpinEntity = useCallback((entityId: number) => {
    setPinnedEntities((prev) => prev.filter((p) => p.entity?.id !== entityId));
  }, []);

  const closeActive = useCallback(() => {
    setSelectedEntity(null);
  }, []);

  const isPinned = useCallback(
    (entityId: number) => pinnedEntities.some((p) => p.entity?.id === entityId),
    [pinnedEntities],
  );

  useEffect(() => {
    if (!subgraphData) return;
    subgraphRef.current = subgraphData;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
    }
    const w = canvas.width || 800;
    const h = canvas.height || 600;
    simNodesRef.current = subgraphData.nodes.map((n) => ({
      ...n,
      x: w * 0.2 + Math.random() * w * 0.6,
      y: h * 0.2 + Math.random() * h * 0.6,
      vx: 0, vy: 0, pinned: false,
    }));
    mergedEdgesRef.current = buildMergedEdges(subgraphData.edges);
    // Hide tooltip on data change
    if (edgeTooltipRef.current) edgeTooltipRef.current.style.display = "none";
    hoveredEdgeGroupRef.current = null;
    settledRef.current = false;
    runningRef.current = true;
    tickRef.current = 0;
    startRAF();
  }, [subgraphData]);

  function startRAF() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  }

  function tick() {
    if (!runningRef.current) return;
    const canvas = canvasRef.current;
    const sg = subgraphRef.current;
    const nodes = simNodesRef.current;
    const merged = mergedEdgesRef.current;
    if (!canvas || !sg || nodes.length === 0) return;

    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
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
      tickRef.current++;
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

      if (ke < 0.1 * n || tickRef.current >= 300) {
        settledRef.current = true;
        runningRef.current = false;
        break;
      }
    }

    drawCanvas(canvas, sg, nodes);

    // Auto-fit on first settle
    if (settledRef.current && tickRef.current <= 310) {
      fitGraph();
    }

    if (runningRef.current) rafRef.current = requestAnimationFrame(tick);
    else drawCanvas(canvas, sg, nodes);
  }

  function drawCanvas(canvas: HTMLCanvasElement, _sg: SubgraphData, nodes: SimNode[]) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const selNode = selectedNodeRef.current;
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

    const highlighted = highlightNodesRef.current;
    const merged = mergedEdgesRef.current;

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
      const color = typeColor(n.type);
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

    // Labels
    if (settledRef.current) {
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
        const label = n.name.length > 22 ? n.name.slice(0, 21) + "\u2026" : n.name;
        const metrics = ctx.measureText(label);
        ctx.globalAlpha = isActive ? 0.9 : dimAlpha;
        ctx.fillStyle = bgPanel;
        ctx.fillRect(n.x - metrics.width / 2 - 3, n.y + r + 3, metrics.width + 6, 12);
        ctx.globalAlpha = 1;
        ctx.fillStyle = isCore ? fg : isActive ? fgDim : fgDim;
        ctx.fillText(label, n.x, n.y + r + 4);
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
      const sg = subgraphRef.current;
      const nodes = simNodesRef.current;
      if (sg && nodes.length > 0) drawCanvas(canvas, sg, nodes);
    });
    ro.observe(parent);
    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;
    return () => ro.disconnect();
  }, []);

  function canvasPoint(e: React.MouseEvent) {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left - offsetRef.current.x) / scaleRef.current,
      y: (e.clientY - rect.top - offsetRef.current.y) / scaleRef.current,
    };
  }

  function hitTestNode(cx: number, cy: number): number | null {
    const nodes = simNodesRef.current;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = nodeRadius(n.degree) + 4;
      if ((cx - n.x) ** 2 + (cy - n.y) ** 2 <= r * r) return i;
    }
    return null;
  }

  function hitTestEdge(cx: number, cy: number): number | null {
    const nodes = simNodesRef.current;
    const merged = mergedEdgesRef.current;
    const idx = new Map<number, number>();
    nodes.forEach((n, i) => idx.set(n.id, i));
    let best = -1;
    let bestDist = 8;
    for (let gi = 0; gi < merged.length; gi++) {
      const g = merged[gi];
      const ai = idx.get(g.nodeA);
      const bi = idx.get(g.nodeB);
      if (ai === undefined || bi === undefined) continue;
      const d = distToSegment(cx, cy, nodes[ai].x, nodes[ai].y, nodes[bi].x, nodes[bi].y);
      if (d < bestDist) {
        bestDist = d;
        best = gi;
      }
    }
    return best >= 0 ? best : null;
  }

  function redraw() {
    const sg = subgraphRef.current;
    const canvas = canvasRef.current;
    if (sg && canvas) drawCanvas(canvas, sg, simNodesRef.current);
  }

  function onCanvasDown(e: React.MouseEvent) {
    const { x, y } = canvasPoint(e);
    const hit = hitTestNode(x, y);
    if (hit !== null) {
      dragRef.current = { idx: hit, moved: false };
    } else {
      panRef.current = { ox: offsetRef.current.x, oy: offsetRef.current.y, mx: e.clientX, my: e.clientY };
    }
  }

  function onCanvasMove(e: React.MouseEvent) {
    const { x: cx, y: cy } = canvasPoint(e);
    hoverRef.current = hitTestNode(cx, cy);

    const selNode = selectedNodeRef.current;
    const highlighted = highlightNodesRef.current;
    const hasFocus = selNode !== null || highlighted.size > 0;

    // Build activeNodes set (same logic as drawCanvas) for tooltip gating
    const activeNodesForTooltip = new Set<number>();
    if (hasFocus) {
      if (selNode !== null) activeNodesForTooltip.add(selNode);
      for (const hi of highlighted) activeNodesForTooltip.add(hi);
      const mergedEdges = mergedEdgesRef.current;
      const nodes = simNodesRef.current;
      const idxMap = new Map<number, number>();
      nodes.forEach((n, i) => idxMap.set(n.id, i));
      for (const g of mergedEdges) {
        const ai = idxMap.get(g.nodeA);
        const bi = idxMap.get(g.nodeB);
        if (ai === undefined || bi === undefined) continue;
        if (activeNodesForTooltip.has(ai)) activeNodesForTooltip.add(bi);
        if (activeNodesForTooltip.has(bi)) activeNodesForTooltip.add(ai);
      }
    }

    // Edge hover tooltip — only on highlighted edges
    const edgeHit = !dragRef.current && !panRef.current ? hitTestEdge(cx, cy) : null;
    const prevEdgeGrp = hoveredEdgeGroupRef.current;
    hoveredEdgeGroupRef.current = edgeHit;
    const tooltip = edgeTooltipRef.current;

    if (tooltip) {
      let showTooltip = false;
      if (hasFocus && edgeHit !== null && simNodesRef.current.length > 0) {
        const g = mergedEdgesRef.current[edgeHit];
        if (g && g.relations.length > 0) {
          const nodes = simNodesRef.current;
          const idxMap = new Map<number, number>();
          nodes.forEach((n, i) => idxMap.set(n.id, i));
          const ai = idxMap.get(g.nodeA);
          const bi = idxMap.get(g.nodeB);
          // Only show on edges where both endpoints are active (visually highlighted)
          if (ai !== undefined && bi !== undefined && activeNodesForTooltip.has(ai) && activeNodesForTooltip.has(bi)) {
            const nameA = nodes[ai]?.name ?? "";
            const nameB = nodes[bi]?.name ?? "";
            const rect = (e.target as HTMLElement).getBoundingClientRect();
            const localX = e.clientX - rect.left;
            const localY = e.clientY - rect.top;
            tooltip.style.display = "block";
            const ttWidth = 220;
            const ttLeft = localX + 14 + ttWidth > rect.width ? localX - ttWidth - 8 : localX + 14;
            tooltip.style.left = `${ttLeft}px`;
            tooltip.style.top = `${Math.max(4, localY - 8)}px`;
            tooltip.innerHTML = g.relations.map((r) => {
              const fromName = r.from === g.nodeA ? nameA : nameB;
              const toName = r.to === g.nodeB ? nameB : nameA;
              const dir = "\u2192";
              return `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-names">${fromName} ${dir} ${toName}</span><span class="kv-edge-tooltip-label">${r.label.replace(/_/g, " ")}</span></div>`;
            }).join("");
            showTooltip = true;
          }
        }
      }
      if (!showTooltip) {
        tooltip.style.display = "none";
      }
    }

    if (dragRef.current) {
      dragRef.current.moved = true;
      const n = simNodesRef.current[dragRef.current.idx];
      n.x = cx; n.y = cy; n.vx = 0; n.vy = 0; n.pinned = true;
      if (!runningRef.current) { runningRef.current = true; settledRef.current = false; startRAF(); }
    } else if (panRef.current) {
      offsetRef.current = {
        x: panRef.current.ox + e.clientX - panRef.current.mx,
        y: panRef.current.oy + e.clientY - panRef.current.my,
      };
      redraw();
    } else {
      if (edgeHit !== prevEdgeGrp) redraw();
    }
  }

  function onCanvasUp(e: React.MouseEvent) {
    if (dragRef.current && !dragRef.current.moved) {
      const { x, y } = canvasPoint(e);
      const nodeHit = hitTestNode(x, y);
      if (nodeHit !== null) {
        if (selectedNodeRef.current === nodeHit) {
          selectedNodeRef.current = null;
        } else {
          selectedNodeRef.current = nodeHit;
          selectedEdgeRef.current = null;
        }
        redraw();
      } else {
        const edgeHit = hitTestEdge(x, y);
        if (edgeHit !== null) {
          if (selectedEdgeRef.current === edgeHit) {
            selectedEdgeRef.current = null;
          } else {
            selectedEdgeRef.current = edgeHit;
            selectedNodeRef.current = null;
          }
          redraw();
        } else {
          if (selectedNodeRef.current !== null || selectedEdgeRef.current !== null) {
            selectedNodeRef.current = null;
            selectedEdgeRef.current = null;
            redraw();
          }
        }
      }
    }
    dragRef.current = null;
    panRef.current = null;
  }

  function onCanvasDblClick(e: React.MouseEvent) {
    const { x, y } = canvasPoint(e);
    const nodeHit = hitTestNode(x, y);
    if (nodeHit !== null) {
      const n = simNodesRef.current[nodeHit];
      void selectEntity(n.id);
      return;
    }
    const edgeHit = hitTestEdge(x, y);
    if (edgeHit !== null) {
      const merged = mergedEdgesRef.current;
      const g = merged[edgeHit];
      if (g) {
        const node = simNodesRef.current.find((n) => n.id === g.nodeA);
        if (node) void selectEntity(node.id);
      }
    }
  }

  function onCanvasWheel(e: React.WheelEvent) {
    e.preventDefault();
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const sc = scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    offsetRef.current = {
      x: mx - (mx - offsetRef.current.x) * (newSc / sc),
      y: my - (my - offsetRef.current.y) * (newSc / sc),
    };
    scaleRef.current = newSc;
    redraw();
  }

  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  function fitGraph() {
    const nodes = simNodesRef.current;
    const canvas = canvasRef.current;
    if (!canvas || nodes.length === 0) return;
    const padding = 60;

    // Percentile-based bounds: ignore the worst 5% outliers on each side
    const xs = nodes.map((nd) => nd.x).sort((a, b) => a - b);
    const ys = nodes.map((nd) => nd.y).sort((a, b) => a - b);
    const lo = Math.floor(nodes.length * 0.03);
    const hi = Math.ceil(nodes.length * 0.97) - 1;
    const minX = xs[lo];
    const maxX = xs[hi];
    const minY = ys[lo];
    const maxY = ys[hi];

    const gw = (maxX - minX) || 1;
    const gh = (maxY - minY) || 1;
    const cw = canvas.width - padding * 2;
    const ch = canvas.height - padding * 2;
    // Minimum scale so nodes are always visible; maximum 2.5 for readability
    const sc = Math.max(0.3, Math.min(cw / gw, ch / gh, 2.5));
    scaleRef.current = sc;
    offsetRef.current = {
      x: (canvas.width - gw * sc) / 2 - minX * sc,
      y: (canvas.height - gh * sc) / 2 - minY * sc,
    };
    redraw();
  }

  function refreshGraph() {
    const nodes = simNodesRef.current;
    const canvas = canvasRef.current;
    if (!canvas || nodes.length === 0) return;
    const w = canvas.width || 800;
    const h = canvas.height || 600;
    for (const n of nodes) {
      n.x = w * 0.2 + Math.random() * w * 0.6;
      n.y = h * 0.2 + Math.random() * h * 0.6;
      n.vx = 0;
      n.vy = 0;
      n.pinned = false;
    }
    tickRef.current = 0;
    settledRef.current = false;
    runningRef.current = true;
    // Reset view
    scaleRef.current = 1;
    offsetRef.current = { x: 0, y: 0 };
    startRAF();
  }

  function zoomGraph(factor: number) {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const sc = scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    offsetRef.current = {
      x: cx - (cx - offsetRef.current.x) * (newSc / sc),
      y: cy - (cy - offsetRef.current.y) * (newSc / sc),
    };
    scaleRef.current = newSc;
    redraw();
  }

  function onGraphSearchChange(value: string) {
    setGraphSearch(value);
    const nodes = simNodesRef.current;
    if (!value.trim()) {
      highlightNodesRef.current = new Set();
      setGraphSearchCount(0);
    } else {
      const q = value.toLowerCase();
      const matched = new Set<number>();
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].name.toLowerCase().includes(q)) matched.add(i);
      }
      highlightNodesRef.current = matched;
      setGraphSearchCount(matched.size);
    }
    redraw();
  }

  function clearGraphSearch() {
    setGraphSearch("");
    highlightNodesRef.current = new Set();
    setGraphSearchCount(0);
    redraw();
  }

  const hasResults = queryResult && queryResult.results.length > 0;
  const hasSubgraph = subgraphData && subgraphData.nodes.length > 0;
  const entityTypes = stats?.types ? Object.keys(stats.types) : [];

  return (
    <div className="kv">
      <div className="kv-search-bar">
        <div className="kv-search-wrap">
          <input
            className="kv-search-input"
            type="text"
            placeholder="Search your knowledge…"
            value={queryText}
            onChange={(e) => onSearchChange(e.target.value)}
          />
          {queryText && (
            <button className="kv-search-clear" onClick={clearSearch}>&times;</button>
          )}
        </div>
        {loading && <span className="kv-search-loading">Searching…</span>}
      </div>

      <div className="kv-filters">
        <button
          className={`kv-pill${typeFilter === null ? " kv-pill--active" : ""}`}
          onClick={() => setTypeFilter(null)}
        >
          All
        </button>
        {entityTypes.map((t) => (
          <button
            key={t}
            className={`kv-pill${typeFilter === t ? " kv-pill--active" : ""}`}
            style={{ "--pill-color": typeColor(t) } as React.CSSProperties}
            onClick={() => setTypeFilter(t)}
          >
            {t} <span className="kv-pill-count">{stats?.types[t] ?? 0}</span>
          </button>
        ))}
        <div className="kv-source-filter">
          <select
            className="kv-source-filter-select"
            value={sourceFilter}
            onChange={(e) => {
              const v = e.target.value as "none" | "file" | "folder";
              if (v === "none") clearSourceFilter();
              else { setSourceFilter(v); setSourcePath(""); }
            }}
          >
            <option value="none">No source filter</option>
            <option value="file">Filter by file</option>
            <option value="folder">Filter by folder</option>
          </select>
          {sourceFilter !== "none" && (
            <div className="kv-source-input-wrap">
              <input
                className="kv-source-input"
                type="text"
                placeholder={sourceFilter === "file" ? "path/to/file.md" : "folder/"}
                value={sourcePath}
                onChange={(e) => {
                  setSourcePath(e.target.value);
                  setShowSourceSuggestions(true);
                  const v = e.target.value.toLowerCase();
                  if (v.length >= 1) {
                    getVaultTree().then((entries) => {
                      const paths = entries
                        .filter(e => {
                          if (sourceFilter === "file") return e.type === "file";
                          return e.type === "dir";
                        })
                        .map(e => e.path)
                        .filter(p => p.toLowerCase().includes(v))
                        .slice(0, 12);
                      setSourceSuggestions(paths);
                    });
                  } else {
                    setSourceSuggestions([]);
                  }
                }}
                onFocus={() => { if (sourcePath.length >= 1) setShowSourceSuggestions(true); }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    setShowSourceSuggestions(false);
                    void applySourceFilter(sourceFilter, sourcePath);
                  }
                }}
              />
              <button className="kv-source-go" onClick={() => void applySourceFilter(sourceFilter, sourcePath)}>Go</button>
              <button className="kv-source-clear" onClick={clearSourceFilter}>&times;</button>
              {showSourceSuggestions && sourceSuggestions.length > 0 && (
                <div className="kv-source-suggestions">
                  {sourceSuggestions.map(s => (
                    <button
                      key={s}
                      className="kv-source-suggestion"
                      onClick={() => {
                        setSourcePath(s);
                        setShowSourceSuggestions(false);
                        void applySourceFilter(sourceFilter, s);
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {stats && stats.enabled && (
        <div className="kv-stats">
          <span>{stats.entities} entities</span>
          <span className="kv-stats-dot" />
          <span>{stats.triples} relations</span>
          <span className="kv-stats-dot" />
          <span>{stats.component_count} groups</span>
        </div>
      )}

      <div className="kv-main" ref={mainRef}>
        <div className="kv-evidence" style={{ flex: `0 0 ${splitRatio * 100}%` }}>
          <div className="kv-evidence-top">
            {!hasResults && !loading && (
              <div className="kv-landing">
                <div className="kv-landing-header">
                  <h3 className="kv-landing-title">Top Entities</h3>
                  <input
                    className="kv-entity-filter"
                    type="text"
                    placeholder="Filter…"
                    value={entityFilter}
                    onChange={(e) => setEntityFilter(e.target.value)}
                  />
                </div>
                <div className="kv-entity-grid">
                  {topEntities
                    .filter((e) => {
                      if (typeFilter !== null && e.type !== typeFilter) return false;
                      if (entityFilter && !e.name.toLowerCase().includes(entityFilter.toLowerCase())) return false;
                      return true;
                    })
                    .map((e) => (
                      <button
                        key={e.id}
                        className="kv-entity-card"
                        onClick={() => void selectEntity(e.id)}
                      >
                        <span className="kv-entity-dot" style={{ background: typeColor(e.type) }} />
                        <span className="kv-entity-name">{e.name}</span>
                        <span className="kv-entity-type">{e.type}</span>
                        <span className="kv-entity-degree">{e.degree}</span>
                      </button>
                    ))}
                </div>
              </div>
            )}

            {loading && <div className="kv-loading">Searching...</div>}

            {hasResults && (
              <div className="kv-results">
                {queryResult!.results.map((r, i) => (
                  <div key={r.chunk_id + i} className="kv-evidence-card">
                    <div className="kv-evidence-header">
                      <button
                        className="kv-evidence-source"
                        onClick={() => setPreviewPath(r.source_path)}
                      >
                        {r.source_path} &rsaquo; {r.heading}
                      </button>
                      <span className={`kv-evidence-badge kv-evidence-badge--${r.source}`}>
                        {r.source}
                      </span>
                      <span className="kv-evidence-score">{(r.score * 100).toFixed(0)}%</span>
                    </div>
                    <p className="kv-evidence-snippet">{r.content.slice(0, 300)}</p>
                    {r.related_entities.length > 0 && (
                      <div className="kv-evidence-entities">
                        {r.related_entities.slice(0, 8).map((name) => (
                          <span key={name} className="kv-entity-tag">{name}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {(selectedEntity || pinnedEntities.length > 0) && (
            <div className="kv-cards">
              {selectedEntity && selectedEntity.entity && (() => {
                const se = selectedEntity;
                const seId = se.entity!.id;
                return (
                  <EntityDetailCard
                    key={`sel-${seId}`}
                    detail={se}
                    pinned={isPinned(seId)}
                    onPin={() => pinEntity(se)}
                    onUnpin={() => unpinEntity(seId)}
                    onClose={closeActive}
                    onSelectEntity={(id) => void selectEntity(id)}
                    onPreview={setPreviewPath}
                  />
                );
              })()}
              {pinnedEntities
                .filter((p) => p.entity && p.entity.id !== selectedEntity?.entity?.id)
                .map((p) => (
                  <EntityDetailCard
                    key={`pin-${p.entity!.id}`}
                    detail={p}
                    pinned={true}
                    onPin={() => {}}
                    onUnpin={() => unpinEntity(p.entity!.id)}
                    onClose={() => unpinEntity(p.entity!.id)}
                    onSelectEntity={(id) => void selectEntity(id)}
                    onPreview={setPreviewPath}
                  />
                ))}
            </div>
          )}
        </div>

        <div
          className="kv-divider"
          onMouseDown={(e) => {
            e.preventDefault();
            splitDragRef.current = { startX: e.clientX, startRatio: splitRatio };
            const onMove = (ev: MouseEvent) => {
              if (!splitDragRef.current || !mainRef.current) return;
              const totalW = mainRef.current.clientWidth;
              const dx = ev.clientX - splitDragRef.current.startX;
              const next = Math.max(0.2, Math.min(0.8, splitDragRef.current.startRatio + dx / totalW));
              setSplitRatio(next);
            };
            const onUp = () => {
              splitDragRef.current = null;
              window.removeEventListener("mousemove", onMove);
              window.removeEventListener("mouseup", onUp);
            };
            window.addEventListener("mousemove", onMove);
            window.addEventListener("mouseup", onUp);
          }}
        />

        <div className="kv-graph">
          <canvas
            ref={canvasRef}
            className="kv-canvas"
            onMouseDown={onCanvasDown}
            onMouseMove={onCanvasMove}
            onMouseUp={onCanvasUp}
            onDoubleClick={onCanvasDblClick}
            onWheel={onCanvasWheel}
            onMouseLeave={() => {
              if (edgeTooltipRef.current) edgeTooltipRef.current.style.display = "none";
              hoveredEdgeGroupRef.current = null;
              hoverRef.current = null;
              redraw();
            }}
          />
          <div ref={edgeTooltipRef} className="kv-edge-tooltip" />
          <div className="kv-graph-toolbar">
            <div className="kv-graph-search">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="7" cy="7" r="5" />
                <line x1="11" y1="11" x2="14" y2="14" />
              </svg>
              <input
                className="kv-graph-search-input"
                type="text"
                placeholder="Find entity…"
                value={graphSearch}
                onChange={(e) => onGraphSearchChange(e.target.value)}
                disabled={!hasSubgraph}
              />
              {graphSearch && (
                <span className="kv-graph-search-count">{graphSearchCount}</span>
              )}
              {graphSearch && (
                <button className="kv-graph-search-clear" onClick={clearGraphSearch}>&times;</button>
              )}
            </div>
            <div className="kv-graph-tools">
              <button className="kv-tool-btn" onClick={refreshGraph} title="Restart layout" disabled={!hasSubgraph}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M1 1v5h5" />
                  <path d="M3.5 11A6 6 0 1 0 4.5 4.5L1 6" />
                </svg>
              </button>
              <button className="kv-tool-btn" onClick={fitGraph} title="Fit to view" disabled={!hasSubgraph}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="1" y="1" width="6" height="6" rx="1" />
                  <rect x="9" y="1" width="6" height="6" rx="1" />
                  <rect x="1" y="9" width="6" height="6" rx="1" />
                  <rect x="9" y="9" width="6" height="6" rx="1" />
                </svg>
              </button>
              <button className="kv-tool-btn" onClick={() => zoomGraph(1.3)} title="Zoom in" disabled={!hasSubgraph}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="7" cy="7" r="5" />
                  <line x1="7" y1="5" x2="7" y2="9" />
                  <line x1="5" y1="7" x2="9" y2="7" />
                  <line x1="11" y1="11" x2="14" y2="14" />
                </svg>
              </button>
              <button className="kv-tool-btn" onClick={() => zoomGraph(0.7)} title="Zoom out" disabled={!hasSubgraph}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="7" cy="7" r="5" />
                  <line x1="5" y1="7" x2="9" y2="7" />
                  <line x1="11" y1="11" x2="14" y2="14" />
                </svg>
              </button>
            </div>
          </div>
          {!hasSubgraph && (
            <div className="kv-graph-empty">
              {sourceFilter === "file" && sourcePath && subgraphData !== null && !loading ? (
                <>
                  <p>No entities found for this file.</p>
                  {onStartGraphIndex && (
                    <button className="kv-index-file-btn" onClick={() => onStartGraphIndex(sourcePath)}>
                      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
                        <polyline points="7,8 10,4 13,8" />
                        <line x1="10" y1="4" x2="10" y2="14" />
                      </svg>
                      Index this file
                    </button>
                  )}
                  <span className="kv-graph-empty-hint">Extracts entities and relationships using the LLM</span>
                </>
              ) : loading ? (
                <p>Loading…</p>
              ) : (
                <p>Search or click an entity to explore its knowledge graph</p>
              )}
            </div>
          )}
        </div>
      </div>

      {previewPath && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} onViewEntityGraph={onViewEntityGraph} />
      )}
    </div>
  );
}
