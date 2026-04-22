import { useCallback, useEffect, useRef, useState } from "react";
import {
  knowledgeQuery,
  getKnowledgeStats,
  getKnowledgeEntities,
  getKnowledgeEntity,
  getKnowledgeSubgraph,
  type KnowledgeStats,
  type KnowledgeEntity,
  type KnowledgeQueryResult,
  type EntityDetail,
  type SubgraphData,
} from "../api";
import VaultFilePreview from "./VaultFilePreview";
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
  return Math.max(4, Math.min(14, 4 + Math.log(degree + 1) * 2.5));
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

export default function KnowledgeView() {
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

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simNodesRef = useRef<SimNode[]>([]);
  const subgraphRef = useRef<SubgraphData | null>(null);
  const rafRef = useRef<number | null>(null);
  const runningRef = useRef(false);
  const tickRef = useRef(0);
  const settledRef = useRef(false);
  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef(1);
  const hoverRef = useRef<number | null>(null);
  const selectedNodeRef = useRef<number | null>(null);
  const selectedEdgeRef = useRef<number | null>(null);
  const dragRef = useRef<{ idx: number; moved: boolean } | null>(null);
  const panRef = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const splitDragRef = useRef<{ startX: number; startRatio: number } | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getKnowledgeStats().then(setStats).catch(() => {});
    getKnowledgeEntities({ limit: 200 }).then((r) => setTopEntities(r.entities)).catch(() => {});
  }, []);

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
    if (!canvas || !sg || nodes.length === 0) return;

    tickRef.current++;
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const idx = new Map<number, number>();
    nodes.forEach((n, i) => idx.set(n.id, i));

    const fx = new Float64Array(nodes.length);
    const fy = new Float64Array(nodes.length);

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const d2 = dx * dx + dy * dy + 1;
        const f = 3000 / d2;
        const d = Math.sqrt(d2);
        fx[i] -= f * dx / d; fy[i] -= f * dy / d;
        fx[j] += f * dx / d; fy[j] += f * dy / d;
      }
    }

    for (const e of sg.edges) {
      const ai = idx.get(Number(e.source));
      const bi = idx.get(Number(e.target));
      if (ai === undefined || bi === undefined) continue;
      const dx = nodes[bi].x - nodes[ai].x;
      const dy = nodes[bi].y - nodes[ai].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (dist - 90) * 0.025;
      fx[ai] += f * dx / dist; fy[ai] += f * dy / dist;
      fx[bi] -= f * dx / dist; fy[bi] -= f * dy / dist;
    }

    for (let i = 0; i < nodes.length; i++) {
      fx[i] += (cx - nodes[i].x) * 0.01;
      fy[i] += (cy - nodes[i].y) * 0.01;
    }

    let ke = 0;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].pinned) continue;
      nodes[i].vx = (nodes[i].vx + fx[i]) * 0.82;
      nodes[i].vy = (nodes[i].vy + fy[i]) * 0.82;
      nodes[i].x += nodes[i].vx;
      nodes[i].y += nodes[i].vy;
      ke += nodes[i].vx * nodes[i].vx + nodes[i].vy * nodes[i].vy;
    }

    if (ke < 0.15 * nodes.length || tickRef.current >= 400) {
      settledRef.current = true;
      runningRef.current = false;
    }

    drawCanvas(canvas, sg, nodes);

    if (runningRef.current) rafRef.current = requestAnimationFrame(tick);
    else drawCanvas(canvas, sg, nodes);
  }

  function drawCanvas(canvas: HTMLCanvasElement, sg: SubgraphData, nodes: SimNode[]) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const hover = hoverRef.current;
    const selNode = selectedNodeRef.current;
    const selEdge = selectedEdgeRef.current;
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

    for (let ei = 0; ei < sg.edges.length; ei++) {
      const e = sg.edges[ei];
      const ai = idx.get(Number(e.source));
      const bi = idx.get(Number(e.target));
      if (ai === undefined || bi === undefined) continue;
      const hlHover = hover === ai || hover === bi;
      const hlSel = selNode === ai || selNode === bi || selEdge === ei;
      const hl = hlHover || hlSel;
      ctx.beginPath();
      ctx.moveTo(nodes[ai].x, nodes[ai].y);
      ctx.lineTo(nodes[bi].x, nodes[bi].y);
      ctx.strokeStyle = hl ? accent : border;
      ctx.lineWidth = hl ? 1.5 : 0.7;
      ctx.globalAlpha = hl ? 1 : 0.4;
      ctx.stroke();
      ctx.globalAlpha = 1;

      if (hl && settledRef.current && e.relation) {
        const mx = (nodes[ai].x + nodes[bi].x) / 2;
        const my = (nodes[ai].y + nodes[bi].y) / 2;
        const label = e.relation.replace(/_/g, " ");
        ctx.font = "9px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        const m = ctx.measureText(label);
        ctx.fillStyle = bgPanel;
        ctx.globalAlpha = 0.8;
        ctx.fillRect(mx - m.width / 2 - 3, my - 13, m.width + 6, 13);
        ctx.globalAlpha = 1;
        ctx.fillStyle = fgDim;
        ctx.fillText(label, mx, my - 2);
      }
    }

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const r = nodeRadius(n.degree);
      const color = typeColor(n.type);
      const isHover = hover === i;
      const isSel = selNode === i;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + (isHover || isSel ? 2 : 0), 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      if (isHover || isSel) {
        ctx.strokeStyle = fg;
        ctx.lineWidth = isSel ? 2 : 1.5;
        ctx.stroke();
      }
    }

    if (settledRef.current) {
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const isHover = hover === i;
        const isSel = selNode === i;
        if (n.degree < 3 && !isHover && !isSel) continue;
        const r = nodeRadius(n.degree);
        const label = n.name.length > 20 ? n.name.slice(0, 19) + "\u2026" : n.name;
        const metrics = ctx.measureText(label);
        ctx.fillStyle = bgPanel;
        ctx.globalAlpha = 0.85;
        ctx.fillRect(n.x - metrics.width / 2 - 3, n.y + r + 3, metrics.width + 6, 13);
        ctx.globalAlpha = 1;
        ctx.fillStyle = (isHover || isSel) ? fg : fgDim;
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
    const sg = subgraphRef.current;
    if (!sg) return null;
    const idx = new Map<number, number>();
    nodes.forEach((n, i) => idx.set(n.id, i));
    let best = -1;
    let bestDist = 8;
    for (let ei = 0; ei < sg.edges.length; ei++) {
      const e = sg.edges[ei];
      const ai = idx.get(Number(e.source));
      const bi = idx.get(Number(e.target));
      if (ai === undefined || bi === undefined) continue;
      const d = distToSegment(cx, cy, nodes[ai].x, nodes[ai].y, nodes[bi].x, nodes[bi].y);
      if (d < bestDist) {
        bestDist = d;
        best = ei;
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
      redraw();
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
      const sg = subgraphRef.current;
      if (!sg) return;
      const edge = sg.edges[edgeHit];
      const srcNode = simNodesRef.current.find((n) => n.id === Number(edge.source));
      if (srcNode) void selectEntity(srcNode.id);
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
            placeholder="Search your knowledge\u2026"
            value={queryText}
            onChange={(e) => onSearchChange(e.target.value)}
          />
          {queryText && (
            <button className="kv-search-clear" onClick={clearSearch}>&times;</button>
          )}
        </div>
        {loading && <span className="kv-search-loading">Searching\u2026</span>}
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
                    placeholder="Filter\u2026"
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
          />
          {!hasSubgraph && (
            <div className="kv-graph-empty">
              <p>Search or click an entity to explore its knowledge graph</p>
            </div>
          )}
        </div>
      </div>

      {previewPath && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} />
      )}
    </div>
  );
}
