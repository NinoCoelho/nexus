/**
 * 3D variant of the Knowledge subgraph canvas, built on top of
 * `react-force-graph-3d` (Three.js force-directed simulation in 3D).
 *
 * Kept side-by-side with the 2D `SubgraphCanvas` so users can compare.
 * Toggle via the KnowledgeView 2D/3D button.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import type { SubgraphData } from "../../api";

interface Props {
  subgraphData: SubgraphData | null;
  hasSubgraph: boolean;
  loading: boolean;
  sourceFilter: "none" | "file" | "folder";
  sourcePath: string;
  onStartGraphIndex?: (path: string) => void;
  onSelectEntity: (id: number) => void;
  graphSearch: string;
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
  onToggleViewMode?: () => void;
  viewMode: "2d" | "3d";
}

const TYPE_COLORS: Record<string, string> = {
  person: "#c9a84c",
  project: "#b87333",
  concept: "#7a5e9e",
  technology: "#5e7a9e",
  decision: "#9e4a3a",
  resource: "#4a9e7a",
};
const DEFAULT_COLOR = "#7a9e7e";

interface Node3D {
  id: number;
  name: string;
  type: string;
  degree: number;
}

interface RelationEntry {
  relation: string;
  from: number;
  to: number;
}

interface Link3D {
  source: number;
  target: number;
  relations: RelationEntry[];
}

export function SubgraphCanvas3D({
  subgraphData,
  hasSubgraph,
  loading,
  sourceFilter,
  sourcePath,
  onStartGraphIndex,
  onSelectEntity,
  graphSearch,
  fullscreen,
  onToggleFullscreen,
  onToggleViewMode,
  viewMode,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<unknown>(null);
  const callFit = useCallback(() => {
    const fg = fgRef.current as { zoomToFit?: (ms?: number, padding?: number) => void } | null;
    fg?.zoomToFit?.(600, 60);
  }, []);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 600 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
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

  const nodeThreeObject = useCallback(
    (raw: object) => {
      const node = raw as Node3D;
      const baseColor = TYPE_COLORS[node.type] ?? DEFAULT_COLOR;
      const isMatch = matchedIds.size > 0 && matchedIds.has(node.id);
      const radius = 3 + Math.log(node.degree + 1) * 1.4;

      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 16, 16),
        new THREE.MeshLambertMaterial({
          color: isMatch ? "#d4855c" : baseColor,
          emissive: isMatch ? "#d4855c" : "#000000",
          emissiveIntensity: isMatch ? 0.6 : 0,
        }),
      );

      const label = node.name.length > 22 ? node.name.slice(0, 21) + "…" : node.name;
      const sprite = makeTextSprite(label, isMatch);
      sprite.position.set(0, radius + 5, 0);

      const group = new THREE.Group();
      group.add(sphere);
      group.add(sprite);
      return group;
    },
    [matchedIds],
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
    tt.innerHTML = link.relations
      .map((r) => {
        const fromName = nameById.get(r.from) ?? "?";
        const toName = nameById.get(r.to) ?? "?";
        const label = (r.relation || "").replace(/_/g, " ");
        return `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-names">${escapeHtml(fromName)} → ${escapeHtml(toName)}</span><span class="kv-edge-tooltip-label">${escapeHtml(label)}</span></div>`;
      })
      .join("");
  }, [nameById]);

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
    // Hide edge tooltip when hovering nodes
    hoveredLinkRef.current = null;
    if (tooltipRef.current) tooltipRef.current.style.display = "none";
  }, []);

  useEffect(() => {
    if (!data.nodes.length) return;
    const t = setTimeout(callFit, 500);
    return () => clearTimeout(t);
  }, [data.nodes.length]);

  // Tighten layout (shorter links, stronger charge) and enable cursor-pointed
  // zoom on the underlying Three.js OrbitControls. Runs once after mount via
  // a deferred microtask so the d3 simulation is fully wired before we touch
  // its forces — modifying them before first tick raises "tick of undefined".
  useEffect(() => {
    let cancelled = false;
    const apply = () => {
      if (cancelled) return;
      const fg = fgRef.current as {
        d3Force?: (kind: string) => { distance?: (n: number) => unknown; strength?: (n: number) => unknown } | undefined;
        controls?: () => { zoomToCursor?: boolean; screenSpacePanning?: boolean } | undefined;
      } | null;
      if (!fg) return;
      try {
        const link = fg.d3Force?.("link");
        link?.distance?.(28);
        const charge = fg.d3Force?.("charge");
        charge?.strength?.(-90);
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

  return (
    <div className="kv-graph kv-graph-3d" ref={wrapRef} onMouseMove={onWrapperMouseMove}>
      {hasSubgraph ? (
        <ForceGraph3D
          ref={fgRef as React.MutableRefObject<undefined> | undefined}
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
          enableNodeDrag
          linkHoverPrecision={4}
          onNodeClick={(n: object) => onSelectEntity((n as Node3D).id)}
          onLinkHover={onLinkHover}
          onNodeHover={onNodeHover}
        />
      ) : (
        <div className="kv-graph-empty">
          {sourceFilter === "file" && sourcePath && !loading ? (
            <>
              <p>No entities found for this file.</p>
              {onStartGraphIndex && (
                <button className="kv-index-file-btn" onClick={() => onStartGraphIndex(sourcePath)}>
                  Index this file
                </button>
              )}
            </>
          ) : loading ? (
            <p>Loading…</p>
          ) : (
            <p>Search or click an entity to explore its knowledge graph</p>
          )}
        </div>
      )}
      <div ref={tooltipRef} className="kv-edge-tooltip" style={{ display: "none" }} />

      <div className="kv-graph-toolbar">
        <div className="kv-graph-tools">
          {onToggleViewMode && (
            <button
              className="kv-tool-btn kv-tool-btn--label"
              onClick={onToggleViewMode}
              title={viewMode === "3d" ? "Switch to 2D" : "Switch to 3D"}
            >
              {viewMode === "3d" ? "2D" : "3D"}
            </button>
          )}
          <button
            className="kv-tool-btn"
            onClick={() => callFit()}
            title="Fit to view"
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
  const fontSize = 32;
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
  const scale = 0.18;
  sprite.scale.set(canvas.width * scale, canvas.height * scale, 1);
  return sprite;
}
