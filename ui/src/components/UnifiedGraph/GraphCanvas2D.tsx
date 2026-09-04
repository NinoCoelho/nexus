/**
 * GraphCanvas2D — the default graph renderer.
 *
 * Canvas-2D force graph: pan/zoom with the mouse, draggable nodes, degree-
 * sized circles, labels with zoom-based LOD. No WebGL context is needed, so
 * it sidesteps the single-context limit of sandboxed webviews entirely.
 * Exposes the same GraphCanvasHandle as GraphCanvas3D.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type {
  ContextMenuItem,
  GraphCanvasHandle,
  UnifiedGraphData,
  UnifiedLink,
  UnifiedNode,
} from "./types";
import type { GraphSettings } from "./graphSettings";
import { GraphContextMenu, useLinkTooltip, usePendingFit } from "./canvasCommon";

interface Props {
  data: UnifiedGraphData;
  selectedId: string | null;
  /** Highlight from the main "Search your knowledge". */
  search: string;
  /** Highlight from the floating /-find widget. */
  findQuery?: string;
  settings: GraphSettings;
  onSelect: (node: UnifiedNode | null) => void;
  onNodeRightClick?: (node: UnifiedNode, x: number, y: number) => void;
  contextMenu?: { node: UnifiedNode; items: ContextMenuItem[]; x: number; y: number } | null;
  onCloseContextMenu?: () => void;
  emptyState?: React.ReactNode;
}

type FgInstance = {
  zoomToFit?: (ms?: number, padding?: number) => void;
  centerAt?: (x?: number, y?: number, ms?: number) => void;
  zoom?: (z?: number, ms?: number) => number;
  d3Force?: (kind: string) => { distance?: (n: number) => unknown; strength?: (n: number) => unknown } | undefined;
  d3ReheatSimulation?: () => void;
};

const FG_COLOR = "#ece8e1";
const FIND_COLOR = "#5cf0ff";
const SELECT_COLOR = "#ffd06a";

export const GraphCanvas2D = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas2D(
  { data, selectedId, search, findQuery, settings, onSelect, onNodeRightClick, contextMenu, onCloseContextMenu, emptyState },
  ref,
) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<FgInstance | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 600 });

  const tooltip = useLinkTooltip();
  const pendingFitRef = usePendingFit(data);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const callFit = useCallback(() => fgRef.current?.zoomToFit?.(600, 50), []);
  const callReheat = useCallback(() => fgRef.current?.d3ReheatSimulation?.(), []);

  const callZoom = useCallback((factor: number) => {
    const fg = fgRef.current;
    if (!fg?.zoom) return;
    const z = fg.zoom?.() ?? 1;
    const next = z / factor;
    if (next < 0.05 || next > 200) return;
    fg.zoom?.(next, 300);
  }, []);
  const flyTo = useCallback((nodeId: string) => {
    const fg = fgRef.current;
    if (!fg?.centerAt) return;
    const node = data.nodes.find((n) => n.id === nodeId) as (UnifiedNode & { x?: number; y?: number }) | undefined;
    if (!node || node.x == null || node.y == null) return;
    fg.centerAt(node.x, node.y, 600);
    const cur = fg.zoom?.() ?? 1;
    fg.zoom?.(Math.max(cur, 2), 600);
  }, [data.nodes]);

  const flyToNearestMatch = useCallback((nodeIds: string[]) => {
    const fg = fgRef.current;
    if (!fg?.centerAt || nodeIds.length === 0) return;
    if (nodeIds.length === 1) { flyTo(nodeIds[0]); return; }
    // centerAt with no args is the getter for the current viewport center.
    const center = (fg.centerAt as unknown as () => { x?: number; y?: number } | undefined)();
    if (!center || center.x == null || center.y == null) { flyTo(nodeIds[0]); return; }
    const ids = new Set(nodeIds);
    let best: { id: string; dist: number } | null = null;
    for (const raw of data.nodes) {
      if (!ids.has(raw.id)) continue;
      const n = raw as UnifiedNode & { x?: number; y?: number };
      if (n.x == null || n.y == null) continue;
      const dx = n.x - center.x!, dy = n.y - center.y!;
      const d = dx * dx + dy * dy;
      if (!best || d < best.dist) best = { id: n.id, dist: d };
    }
    if (best) flyTo(best.id);
  }, [data.nodes, flyTo]);

  useImperativeHandle(ref, () => ({
    fit: callFit,
    reheat: callReheat,
    zoomIn: () => callZoom(1.3),
    zoomOut: () => callZoom(1 / 1.3),
    flyTo,
    flyToNearestMatch,
  }), [callFit, callReheat, callZoom, flyTo, flyToNearestMatch]);

  // d3 force tuning — mirrors the 3D canvas so both renderers lay out alike.
  useEffect(() => {
    if (!data.nodes.length) return;
    let cancelled = false;
    const apply = () => {
      if (cancelled) return;
      const fg = fgRef.current;
      if (!fg) return;
      try {
        const n = data.nodes.length;
        const linkDist = Math.max(40, Math.min(80, 30 + Math.sqrt(n) * 3)) * settings.linkDistance;
        const charge = -Math.max(120, Math.min(400, 60 + Math.sqrt(n) * 18)) * settings.chargeStrength;
        fg.d3Force?.("link")?.distance?.(linkDist);
        fg.d3Force?.("charge")?.strength?.(charge);
        fg.d3ReheatSimulation?.();
      } catch { /* ignore */ }
    };
    const id = setTimeout(apply, 0);
    return () => { cancelled = true; clearTimeout(id); };
  }, [data.nodes.length, settings.linkDistance, settings.chargeStrength]);

  // Keyboard shortcuts (same as 3D)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (document.activeElement as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "f" && !e.metaKey && !e.ctrlKey && !e.altKey) callFit();
      else if (e.key === "r" && !e.metaKey && !e.ctrlKey && !e.altKey) callReheat();
      else if (e.key === "Escape") onSelect(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [callFit, callReheat, onSelect]);

  const handleEngineStop = useCallback(() => {
    if (!pendingFitRef.current) return;
    pendingFitRef.current = false;
    callFit();
  }, [callFit, pendingFitRef]);

  const matchedIds = useRef<Set<string>>(new Set());
  const findMatchedIds = useRef<Set<string>>(new Set());
  matchedIds.current = new Set(
    search.trim()
      ? data.nodes.filter((n) => n.label.toLowerCase().includes(search.trim().toLowerCase())).map((n) => n.id)
      : [],
  );
  findMatchedIds.current = new Set(
    (findQuery ?? "").trim()
      ? data.nodes.filter((n) => n.label.toLowerCase().includes((findQuery ?? "").trim().toLowerCase())).map((n) => n.id)
      : [],
  );

  // Node radius helper shared by paint + hit-test paint.
  const radiusFor = useCallback((node: UnifiedNode): number => {
    return (2.5 + Math.log(node.degree + 1) * 1.3 + (node.radiusBoost ?? 0) * 1.5) * settings.nodeSize;
  }, [settings.nodeSize]);

  const nodeCanvasObject = useCallback((raw: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const node = raw as UnifiedNode;
    const r = radiusFor(node) / globalScale;
    const isMatch = matchedIds.current.has(node.id);
    const isFindMatch = findMatchedIds.current.has(node.id);
    const isSelected = node.id === selectedId;

    ctx.beginPath();
    ctx.arc(0, 0, r, 0, 2 * Math.PI);
    ctx.fillStyle = isSelected ? SELECT_COLOR : isFindMatch ? FIND_COLOR : isMatch ? FG_COLOR : (node.color ?? "#7a9e7e");
    ctx.globalAlpha = settings.nodeOpacity;
    ctx.fill();
    ctx.globalAlpha = 1;

    if (isFindMatch || isMatch) {
      // halo ring so matches pop on busy graphs
      ctx.beginPath();
      ctx.arc(0, 0, r + 2 / globalScale, 0, 2 * Math.PI);
      ctx.strokeStyle = isFindMatch ? FIND_COLOR : FG_COLOR;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1.5 / globalScale;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (isSelected) {
      ctx.beginPath();
      ctx.arc(0, 0, r + 3.5 / globalScale, 0, 2 * Math.PI);
      ctx.strokeStyle = SELECT_COLOR;
      ctx.lineWidth = 1.5 / globalScale;
      ctx.stroke();
    }

    // Label LOD: always for selected/hovered-ish nodes, otherwise only when
    // the viewport is zoomed in enough (or the graph is small).
    const dense = data.nodes.length > 150;
    const showLabel = isSelected || isFindMatch || !dense || globalScale >= 0.9;
    if (showLabel) {
      const label = node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label;
      const fontSize = Math.max(10 / globalScale, 3.5) * settings.labelScale;
      ctx.font = `${fontSize}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const fy = isSelected ? r + 3 / globalScale : r + 1.5 / globalScale;
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillText(label, 0.5 / globalScale, fy + 0.5 / globalScale);
      ctx.fillStyle = isSelected || isFindMatch || isMatch ? FG_COLOR : "rgba(236,232,225,0.82)";
      ctx.fillText(label, 0, fy);
    }
  }, [selectedId, settings.nodeOpacity, settings.nodeSize, settings.labelScale, radiusFor, data.nodes.length]);

  const nodePointerAreaPaint = useCallback((raw: object, color: string, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const node = raw as UnifiedNode;
    ctx.beginPath();
    ctx.arc(0, 0, radiusFor(node) / globalScale + 2 / globalScale, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
  }, [radiusFor]);

  const linkColor = useCallback((raw: object) => {
    const l = raw as UnifiedLink;
    return l.color || "rgba(180,180,180,0.45)";
  }, []);

  const onNodeClick = useCallback((raw: object) => {
    onSelect(raw as UnifiedNode);
  }, [onSelect]);

  const onNodeRC = useCallback((raw: object, e: MouseEvent) => {
    e.preventDefault();
    const node = raw as UnifiedNode;
    const wrap = wrapRef.current;
    if (!wrap || !onNodeRightClick) return;
    const rect = wrap.getBoundingClientRect();
    onNodeRightClick(node, e.clientX - rect.left, e.clientY - rect.top);
  }, [onNodeRightClick]);

  const hasData = data.nodes.length > 0;

  return (
    <div className="ug-canvas" ref={wrapRef}>
      <ForceGraph2D
        ref={fgRef as unknown as React.MutableRefObject<undefined> | undefined}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeCanvasObject={nodeCanvasObject}
        nodePointerAreaPaint={nodePointerAreaPaint}
        linkColor={linkColor}
        linkWidth={settings.linkWidth}
        linkCurvature={settings.linkCurvature}
        linkDirectionalParticles={data.links.length <= 400 ? 1 : 0}
        linkDirectionalParticleSpeed={settings.particleSpeed}
        linkDirectionalParticleWidth={settings.particleWidth}
        enableNodeDrag={true}
        cooldownTime={6000}
        onNodeClick={onNodeClick}
        onNodeRightClick={onNodeRC}
        onBackgroundClick={() => onSelect(null)}
        onLinkHover={tooltip.onLinkHover}
        onNodeHover={tooltip.onNodeHover}
        onEngineStop={handleEngineStop}
      />

      <div ref={tooltip.tooltipRef} className="kv-edge-tooltip" style={{ display: "none" }} />

      {!hasData && emptyState && (
        <div className="ug-empty">{emptyState}</div>
      )}

      {contextMenu && <GraphContextMenu menu={contextMenu} onClose={() => onCloseContextMenu?.()} />}
    </div>
  );
});
