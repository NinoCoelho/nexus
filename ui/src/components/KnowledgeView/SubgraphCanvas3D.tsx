import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import type { SubgraphData } from "../../api";
import { TYPE_COLORS, DEFAULT_TYPE_COLOR } from "./typeColors";

interface Props {
  subgraphData: SubgraphData | null;
  hasSubgraph: boolean;
  loading: boolean;
  sourceFilter: "none" | "file" | "folder";
  sourcePath: string;
  onStartGraphIndex?: (path: string) => void;
  onSelectEntity: (id: number) => void;
  graphSearch: string;
  onGraphSearchChange: (v: string) => void;
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
  hopDepth: number;
  onHopDepthChange: (h: number) => void;
  onExampleQuery: (q: string) => void;
  onSpawnSession?: (entityId: number, entityName: string) => void;
}

interface Node3D {
  id: number;
  name: string;
  type: string;
  degree: number;
  x?: number;
  y?: number;
  z?: number;
}

interface RelationEntry {
  relation: string;
  from: number;
  to: number;
}

interface Link3D {
  source: number | Node3D;
  target: number | Node3D;
  relations: RelationEntry[];
}

type FgInstance = {
  zoomToFit?: (ms?: number, padding?: number) => void;
  cameraPosition?: (pos?: { x: number; y: number; z: number }, lookAt?: object, ms?: number) => { x: number; y: number; z: number };
  d3Force?: (kind: string) => { distance?: (n: number) => unknown; strength?: (n: number) => unknown } | undefined;
  controls?: () => { zoomToCursor?: boolean; screenSpacePanning?: boolean } | undefined;
  d3ReheatSimulation?: () => void;
  zoom?: (factor: number, ms?: number) => void;
};

interface ContextMenu {
  nodeId: number;
  nodeName: string;
  x: number;
  y: number;
}

const EXAMPLE_QUERIES = ["What am I working on?", "Who are the key people?", "Recent decisions"];

export function SubgraphCanvas3D({
  subgraphData,
  hasSubgraph,
  loading,
  sourceFilter,
  sourcePath,
  onStartGraphIndex,
  onSelectEntity,
  graphSearch,
  onGraphSearchChange,
  fullscreen,
  onToggleFullscreen,
  hopDepth,
  onHopDepthChange,
  onExampleQuery,
  onSpawnSession,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<FgInstance | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 600 });
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(() => {
    try { return JSON.parse(sessionStorage.getItem("kv:selectedNodeId") ?? "null"); } catch { return null; }
  });
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Persist selectedNodeId to sessionStorage
  useEffect(() => {
    try {
      if (selectedNodeId === null) sessionStorage.removeItem("kv:selectedNodeId");
      else sessionStorage.setItem("kv:selectedNodeId", JSON.stringify(selectedNodeId));
    } catch { /* ignore */ }
  }, [selectedNodeId]);

  // Persist hopDepth to sessionStorage
  useEffect(() => {
    try { sessionStorage.setItem("kv:hopDepth", String(hopDepth)); } catch { /* ignore */ }
  }, [hopDepth]);

  const callFit = useCallback(() => {
    fgRef.current?.zoomToFit?.(600, 60);
  }, []);

  const callReheat = useCallback(() => {
    fgRef.current?.d3ReheatSimulation?.();
  }, []);

  const callZoomIn = useCallback(() => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition) return;
    const pos = fg.cameraPosition();
    if (!pos) return;
    const dist = Math.hypot(pos.x, pos.y, pos.z);
    const factor = 0.7;
    const newDist = dist * factor;
    if (newDist < 5) return;
    const scale = newDist / dist;
    fg.cameraPosition({ x: pos.x * scale, y: pos.y * scale, z: pos.z * scale }, undefined, 400);
  }, []);

  const callZoomOut = useCallback(() => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition) return;
    const pos = fg.cameraPosition();
    if (!pos) return;
    const dist = Math.hypot(pos.x, pos.y, pos.z);
    const factor = 1.3;
    const newDist = dist * factor;
    const scale = newDist / dist;
    fg.cameraPosition({ x: pos.x * scale, y: pos.y * scale, z: pos.z * scale }, undefined, 400);
  }, []);

  const data = useMemo(() => {
    if (!subgraphData) return { nodes: [] as Node3D[], links: [] as Link3D[] };
    const groups = new Map<string, Link3D>();
    for (const e of subgraphData.edges) {
      const lo = Math.min(e.source, e.target);
      const hi = Math.max(e.source, e.target);
      const key = `${lo}|${hi}`;
      let g = groups.get(key);
      if (!g) {
        g = { source: lo, target: hi, relations: [] };
        groups.set(key, g);
      }
      g.relations.push({ relation: e.relation || "", from: e.source, to: e.target });
    }
    return {
      nodes: subgraphData.nodes.map((n) => ({ id: n.id, name: n.name, type: n.type, degree: n.degree })),
      links: Array.from(groups.values()),
    };
  }, [subgraphData]);

  const nameById = useMemo(() => {
    const m = new Map<number, string>();
    for (const n of data.nodes) m.set(n.id, n.name);
    return m;
  }, [data.nodes]);

  const search = graphSearch.trim().toLowerCase();
  const matchedIds = useMemo(() => {
    if (!search) return new Set<number>();
    return new Set(data.nodes.filter((n) => n.name.toLowerCase().includes(search)).map((n) => n.id));
  }, [data.nodes, search]);

  const matchCount = matchedIds.size;


  const nodeThreeObject = useCallback(
    (raw: object) => {
      const node = raw as Node3D;
      const baseColor = TYPE_COLORS[node.type] ?? DEFAULT_TYPE_COLOR;
      const isMatch = matchedIds.size > 0 && matchedIds.has(node.id);
      const isSelected = node.id === selectedNodeId;
      const radius = 1.6 + Math.log(node.degree + 1) * 0.7;

      const color = isSelected ? "#ffd06a" : isMatch ? "#d4855c" : baseColor;
      const emissiveIntensity = isSelected ? 0.9 : isMatch ? 0.6 : 0;

      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(isSelected ? radius + 1 : radius, 16, 16),
        new THREE.MeshLambertMaterial({
          color,
          emissive: color,
          emissiveIntensity,
        }),
      );

      const group = new THREE.Group();
      group.add(sphere);

      if (isSelected) {
        const ring = new THREE.Mesh(
          new THREE.SphereGeometry(radius + 1.2, 16, 16),
          new THREE.MeshBasicMaterial({ color: "#ffd06a", wireframe: true, transparent: true, opacity: 0.35 }),
        );
        group.add(ring);
      }

      const label = node.name.length > 22 ? node.name.slice(0, 21) + "…" : node.name;
      const sprite = makeTextSprite(label, isMatch || isSelected);
      sprite.position.set(0, (isSelected ? radius + 1 : radius) + 1.5, 0);
      group.add(sprite);

      return group;
    },
    [matchedIds, selectedNodeId],
  );

  const linkColor = useCallback(() => "rgba(180,180,180,0.4)", []);

  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const mousePosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoveredLinkRef = useRef<Link3D | null>(null);

  const renderTooltip = useCallback(() => {
    const tt = tooltipRef.current;
    if (!tt) return;
    const link = hoveredLinkRef.current;
    if (!link || link.relations.length === 0) {
      tt.style.display = "none";
      return;
    }

    // Determine if we should show full or minimal tooltip
    const ls = typeof link.source === "object" ? (link.source as Node3D).id : link.source as number;
    const lt = typeof link.target === "object" ? (link.target as Node3D).id : link.target as number;
    const isIncident = selectedNodeId !== null && (ls === selectedNodeId || lt === selectedNodeId);

    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const localX = mousePosRef.current.x - rect.left;
    const localY = mousePosRef.current.y - rect.top;
    const ttWidth = 240;
    const ttLeft = localX + 14 + ttWidth > rect.width ? localX - ttWidth - 8 : localX + 14;
    tt.style.left = `${ttLeft}px`;
    tt.style.top = `${Math.max(4, localY - 8)}px`;
    tt.style.display = "block";

    if (isIncident) {
      tt.innerHTML = link.relations
        .map((r) => {
          const fromName = nameById.get(r.from) ?? "?";
          const toName = nameById.get(r.to) ?? "?";
          const label = (r.relation || "").replace(/_/g, " ");
          return `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-names">${escapeHtml(fromName)} → ${escapeHtml(toName)}</span><span class="kv-edge-tooltip-label">${escapeHtml(label)}</span></div>`;
        })
        .join("");
    } else {
      // Minimal tooltip: just show relation type
      const labels = [...new Set(link.relations.map((r) => (r.relation || "").replace(/_/g, " ")))];
      tt.innerHTML = `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-label">${labels.map(escapeHtml).join(", ")}</span></div>`;
    }
  }, [nameById, selectedNodeId]);

  const onWrapperMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      mousePosRef.current = { x: e.clientX, y: e.clientY };
      if (hoveredLinkRef.current) renderTooltip();
    },
    [renderTooltip],
  );

  const onLinkHover = useCallback(
    (link: object | null) => {
      hoveredLinkRef.current = (link as Link3D | null) ?? null;
      renderTooltip();
    },
    [renderTooltip],
  );

  const onNodeHover = useCallback(() => {
    hoveredLinkRef.current = null;
    if (tooltipRef.current) tooltipRef.current.style.display = "none";
  }, []);

  const onNodeClick = useCallback(
    (raw: object) => {
      const node = raw as Node3D;
      setSelectedNodeId(node.id);
      onSelectEntity(node.id);

      // Fly camera to node
      const fg = fgRef.current;
      if (fg?.cameraPosition && node.x != null && node.y != null && node.z != null) {
        const distance = 80;
        const mag = Math.hypot(node.x, node.y, node.z) || 1;
        const distRatio = 1 + distance / mag;
        fg.cameraPosition(
          { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio },
          { x: node.x, y: node.y, z: node.z },
          1000,
        );
      }
    },
    [onSelectEntity],
  );

  const onBackgroundClick = useCallback(() => {
    setSelectedNodeId(null);
    setContextMenu(null);
  }, []);

  const onNodeRightClick = useCallback(
    (raw: object, event: MouseEvent) => {
      event.preventDefault();
      const node = raw as Node3D;
      const wrap = wrapRef.current;
      if (!wrap) return;
      const rect = wrap.getBoundingClientRect();
      setContextMenu({
        nodeId: node.id,
        nodeName: node.name,
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    },
    [],
  );

  // Dismiss context menu on outside click
  useEffect(() => {
    if (!contextMenu) return;
    const handler = () => setContextMenu(null);
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [contextMenu]);

  useEffect(() => {
    if (!data.nodes.length) return;
    // Fallback fit in case onEngineStop is delayed; long enough for the
    // simulation to spread nodes so zoomToFit doesn't zoom into a clump.
    const t = setTimeout(callFit, 2500);
    return () => clearTimeout(t);
  }, [data.nodes.length, callFit]);

  useEffect(() => {
    let cancelled = false;
    const apply = () => {
      if (cancelled) return;
      const fg = fgRef.current;
      if (!fg) return;
      try {
        const link = fg.d3Force?.("link");
        link?.distance?.(18);
        const charge = fg.d3Force?.("charge");
        charge?.strength?.(-45);
      } catch { /* ignore */ }
      try {
        const ctrl = fg.controls?.();
        if (ctrl) {
          ctrl.zoomToCursor = true;
          ctrl.screenSpacePanning = true;
        }
      } catch { /* ignore */ }
    };
    const id = setTimeout(apply, 0);
    return () => { cancelled = true; clearTimeout(id); };
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Only act when canvas is in view
      if (!wrapRef.current) return;
      if (e.key === "f" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const tag = (document.activeElement as HTMLElement)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        callFit();
      } else if (e.key === "r" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const tag = (document.activeElement as HTMLElement)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        callReheat();
      } else if (e.key === "/" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        searchInputRef.current?.focus();
      } else if (e.key === "Escape") {
        setSelectedNodeId(null);
        setContextMenu(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [callFit, callReheat]);

  const graphSearchCount = matchCount;

  return (
    <div className="kv-graph kv-graph-3d" ref={wrapRef} onMouseMove={onWrapperMouseMove}>
      {hasSubgraph ? (
        <ForceGraph3D
          ref={fgRef as unknown as React.MutableRefObject<undefined> | undefined}
          width={size.w}
          height={size.h}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          showNavInfo={false}
          nodeRelSize={4}
          nodeOpacity={0.9}
          nodeThreeObject={nodeThreeObject}
          linkColor={linkColor}
          linkOpacity={0.5}
          linkWidth={0.4}
          linkCurvature={0.15}
          linkDirectionalParticles={2}
          linkDirectionalParticleSpeed={0.005}
          linkDirectionalParticleWidth={1.5}
          enableNodeDrag
          linkHoverPrecision={4}
          onNodeClick={onNodeClick}
          onBackgroundClick={onBackgroundClick}
          onNodeRightClick={onNodeRightClick}
          onLinkHover={onLinkHover}
          onNodeHover={onNodeHover}
          onEngineStop={callFit}
        />
      ) : (
        <div className="kv-graph-empty">
          {loading ? (
            <div className="kv-graph-skeleton" />
          ) : sourceFilter === "file" && sourcePath ? (
            <>
              <p>No entities found for this file.</p>
              {onStartGraphIndex && (
                <button className="kv-index-file-btn" onClick={() => onStartGraphIndex(sourcePath)}>
                  Index this file
                </button>
              )}
            </>
          ) : (
            <>
              <p className="kv-graph-empty-title">Explore your knowledge graph</p>
              <div className="kv-graph-example-queries">
                {EXAMPLE_QUERIES.map((q) => (
                  <button
                    key={q}
                    className="kv-graph-example-btn"
                    onClick={() => onExampleQuery(q)}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      <div ref={tooltipRef} className="kv-edge-tooltip" style={{ display: "none" }} />

      {contextMenu && (
        <div
          className="kv-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="kv-context-menu-item"
            onClick={() => { onSelectEntity(contextMenu.nodeId); setContextMenu(null); }}
          >
            Open entity
          </button>
          <button
            className="kv-context-menu-item"
            onClick={() => {
              if (onSpawnSession) onSpawnSession(contextMenu.nodeId, contextMenu.nodeName);
              else console.log("Spawn session:", contextMenu.nodeId, contextMenu.nodeName);
              setContextMenu(null);
            }}
          >
            Spawn chat about this
          </button>
          <button
            className="kv-context-menu-item"
            onClick={() => {
              navigator.clipboard.writeText(`vault://entities/${encodeURIComponent(contextMenu.nodeName)}`).catch(() => {});
              setContextMenu(null);
            }}
          >
            Copy vault link
          </button>
        </div>
      )}

      <div className="kv-graph-toolbar">
        <div className="kv-graph-search" tabIndex={-1}>
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="7" cy="7" r="5" /><line x1="11.5" y1="11.5" x2="15" y2="15" />
          </svg>
          <input
            ref={searchInputRef}
            className="kv-graph-search-input"
            type="text"
            placeholder="Find entity…"
            value={graphSearch}
            onChange={(e) => onGraphSearchChange(e.target.value)}
            disabled={!hasSubgraph}
          />
          {graphSearchCount > 0 && (
            <span className="kv-graph-search-count">{graphSearchCount}</span>
          )}
          {graphSearch && (
            <button
              className="kv-graph-search-clear"
              onClick={() => onGraphSearchChange("")}
              title="Clear search"
            >
              &times;
            </button>
          )}
        </div>

        {/* Hop depth segmented control */}
        <div className="kv-hop-selector">
          {[1, 2, 3].map((h) => (
            <button
              key={h}
              className={`kv-hop-btn${hopDepth === h ? " kv-hop-btn--active" : ""}`}
              onClick={() => onHopDepthChange(h)}
              title={`${h}-hop neighborhood`}
              disabled={!hasSubgraph}
            >
              {h}
            </button>
          ))}
        </div>

        <div className="kv-graph-tools">
          <button
            className="kv-tool-btn"
            onClick={callReheat}
            title="Re-energize layout (r)"
            disabled={!hasSubgraph}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 8a6 6 0 1 1-1.76-4.24" /><polyline points="14 2 14 6 10 6" />
            </svg>
          </button>
          <button
            className="kv-tool-btn"
            onClick={callZoomIn}
            title="Zoom in"
            disabled={!hasSubgraph}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <line x1="8" y1="3" x2="8" y2="13" /><line x1="3" y1="8" x2="13" y2="8" />
            </svg>
          </button>
          <button
            className="kv-tool-btn"
            onClick={callZoomOut}
            title="Zoom out"
            disabled={!hasSubgraph}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <line x1="3" y1="8" x2="13" y2="8" />
            </svg>
          </button>
          <button
            className="kv-tool-btn"
            onClick={callFit}
            title="Fit to view (f)"
            disabled={!hasSubgraph}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="1" y="1" width="6" height="6" rx="1" />
              <rect x="9" y="1" width="6" height="6" rx="1" />
              <rect x="1" y="9" width="6" height="6" rx="1" />
              <rect x="9" y="9" width="6" height="6" rx="1" />
            </svg>
          </button>
          {onToggleFullscreen && (
            <button
              className="kv-tool-btn"
              onClick={onToggleFullscreen}
              title={fullscreen ? "Exit full view" : "Full view"}
            >
              {fullscreen ? (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 1v4H2" /><path d="M10 1v4h4" /><path d="M6 15v-4H2" /><path d="M10 15v-4h4" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 6V2h4" /><path d="M14 6V2h-4" /><path d="M2 10v4h4" /><path d="M14 10v4h-4" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function makeTextSprite(text: string, highlighted: boolean): THREE.Sprite {
  const padding = 6;
  const fontSize = 22;
  const measure = document.createElement("canvas").getContext("2d")!;
  measure.font = `${fontSize}px system-ui, sans-serif`;
  const textWidth = measure.measureText(text).width;

  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(textWidth + padding * 2);
  canvas.height = fontSize + padding * 2;
  const ctx = canvas.getContext("2d")!;
  ctx.font = `${fontSize}px system-ui, sans-serif`;
  ctx.fillStyle = "rgba(29, 32, 37, 0.85)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = highlighted ? "#ffffff" : "#ece8e1";
  ctx.textBaseline = "top";
  ctx.fillText(text, padding, padding);

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  const scale = 0.05;
  sprite.scale.set(canvas.width * scale, canvas.height * scale, 1);
  return sprite;
}
