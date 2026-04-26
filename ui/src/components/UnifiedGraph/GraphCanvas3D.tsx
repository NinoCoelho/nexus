/**
 * GraphCanvas3D — the single ForceGraph3D instance for the entire graph view.
 *
 * Sandboxed webviews (VS Code, Electron) cap concurrent WebGL contexts at one
 * and forceContextLoss is async, so unmounting/remounting per tab races the
 * GPU and the new context fails. This component is mounted once by
 * UnifiedGraph/index.tsx and stays mounted across mode switches; only its
 * `graphData` and callback props change.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import type {
  ContextMenuItem,
  GraphCanvasHandle,
  UnifiedGraphData,
  UnifiedLink,
  UnifiedNode,
} from "./types";
import { makeGeometry, makeTextSprite, escapeHtml } from "./threeHelpers";

interface Props {
  data: UnifiedGraphData;
  selectedId: string | null;
  search: string;
  onSelect: (node: UnifiedNode | null) => void;
  onNodeRightClick?: (node: UnifiedNode, x: number, y: number) => void;
  contextMenu?: { node: UnifiedNode; items: ContextMenuItem[]; x: number; y: number } | null;
  onCloseContextMenu?: () => void;
  emptyState?: React.ReactNode;
}

type FgInstance = {
  zoomToFit?: (ms?: number, padding?: number) => void;
  cameraPosition?: (
    pos?: { x: number; y: number; z: number },
    lookAt?: object,
    ms?: number,
  ) => { x: number; y: number; z: number };
  d3Force?: (kind: string) => { distance?: (n: number) => unknown; strength?: (n: number) => unknown } | undefined;
  d3ReheatSimulation?: () => void;
  controls?: () => { zoomToCursor?: boolean; screenSpacePanning?: boolean } | undefined;
};

export const GraphCanvas3D = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas3D(
  { data, selectedId, search, onSelect, onNodeRightClick, contextMenu, onCloseContextMenu, emptyState },
  ref,
) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<FgInstance | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const mousePosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoveredLinkRef = useRef<UnifiedLink | null>(null);

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

  const callFit = useCallback(() => fgRef.current?.zoomToFit?.(600, 60), []);
  const callReheat = useCallback(() => fgRef.current?.d3ReheatSimulation?.(), []);

  const callZoom = useCallback((factor: number) => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition) return;
    const pos = fg.cameraPosition();
    if (!pos) return;
    const scale = factor;
    const next = { x: pos.x * scale, y: pos.y * scale, z: pos.z * scale };
    if (Math.hypot(next.x, next.y, next.z) < 5) return;
    fg.cameraPosition(next, undefined, 400);
  }, []);

  const flyTo = useCallback((nodeId: string) => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition) return;
    const node = data.nodes.find((n) => n.id === nodeId) as (UnifiedNode & { x?: number; y?: number; z?: number }) | undefined;
    if (!node || node.x == null || node.y == null || node.z == null) return;
    const distance = 80;
    const mag = Math.hypot(node.x, node.y, node.z) || 1;
    const distRatio = 1 + distance / mag;
    fg.cameraPosition(
      { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio },
      { x: node.x, y: node.y, z: node.z },
      1000,
    );
  }, [data.nodes]);

  useImperativeHandle(ref, () => ({
    fit: callFit,
    reheat: callReheat,
    zoomIn: () => callZoom(0.7),
    zoomOut: () => callZoom(1.3),
    flyTo,
  }), [callFit, callReheat, callZoom, flyTo]);

  // Tune d3 forces and OrbitControls behavior. screenSpacePanning makes
  // right-click drag pan the camera in screen space (rather than world
  // space), which feels natural on 3D scatter plots.
  useEffect(() => {
    let cancelled = false;
    const apply = () => {
      if (cancelled) return;
      const fg = fgRef.current;
      if (!fg) return;
      try {
        fg.d3Force?.("link")?.distance?.(18);
        fg.d3Force?.("charge")?.strength?.(-45);
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
      const tag = (document.activeElement as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "f" && !e.metaKey && !e.ctrlKey && !e.altKey) callFit();
      else if (e.key === "r" && !e.metaKey && !e.ctrlKey && !e.altKey) callReheat();
      else if (e.key === "Escape") onSelect(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [callFit, callReheat, onSelect]);

  // Auto-fit when the data set changes substantially (initial load, mode
  // switch, or selecting a different entity that swaps the subgraph). We
  // detect "substantial" by comparing the current node-id signature: a
  // single user drag never changes node ids, so the camera is never
  // snapped back during interaction.
  //
  // We don't fit on a timer — at 200+ nodes the simulation needs >2s to
  // disperse, so a fixed timeout would zoom into the pile-at-origin. Instead
  // we fit on the FIRST onEngineStop after a data swap, by which time the
  // physics has settled and node positions span their final bounding box.
  // pendingFitRef ensures subsequent engine stops (re-heated by hover or
  // any nudge) don't snap the camera back.
  const lastSigRef = useRef<string>("");
  const pendingFitRef = useRef(false);
  useEffect(() => {
    if (!data.nodes.length) return;
    const sig = data.nodes.map((n) => n.id).sort().join("|");
    if (sig === lastSigRef.current) return;
    lastSigRef.current = sig;
    pendingFitRef.current = true;
  }, [data]);

  const handleEngineStop = useCallback(() => {
    if (!pendingFitRef.current) return;
    pendingFitRef.current = false;
    callFit();
  }, [callFit]);

  const matchedIds = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return new Set<string>();
    return new Set(data.nodes.filter((n) => n.label.toLowerCase().includes(term)).map((n) => n.id));
  }, [search, data.nodes]);

  const nodeThreeObject = useCallback((raw: object) => {
    const node = raw as UnifiedNode;
    const isMatch = matchedIds.size > 0 && matchedIds.has(node.id);
    const isSelected = node.id === selectedId;
    const radius = (1.6 + Math.log(node.degree + 1) * 0.7) + (node.radiusBoost ?? 0);
    const baseColor = node.color ?? "#7a9e7e";
    const color = isSelected ? "#ffd06a" : isMatch ? "#d4855c" : baseColor;
    const emissiveIntensity = isSelected ? 0.9 : isMatch ? 0.6 : 0;

    const mesh = new THREE.Mesh(
      makeGeometry(node.geometry, isSelected ? radius + 1 : radius),
      new THREE.MeshLambertMaterial({ color, emissive: color, emissiveIntensity }),
    );

    const group = new THREE.Group();
    group.add(mesh);

    if (isSelected) {
      const ring = new THREE.Mesh(
        new THREE.SphereGeometry(radius + 1.2, 16, 16),
        new THREE.MeshBasicMaterial({ color: "#ffd06a", wireframe: true, transparent: true, opacity: 0.35 }),
      );
      group.add(ring);
    }

    const label = node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label;
    const sprite = makeTextSprite(label, isMatch || isSelected);
    sprite.position.set(0, (isSelected ? radius + 1 : radius) + 1.5, 0);
    group.add(sprite);
    return group;
  }, [matchedIds, selectedId]);

  const linkColor = useCallback((raw: object) => {
    const l = raw as UnifiedLink;
    return l.color || "rgba(180,180,180,0.45)";
  }, []);

  // Tooltip lives on a separate document-level listener so the wrapper has
  // no React onMouseMove that could capture or interfere with the canvas's
  // native pointer events (which OrbitControls relies on for drag-to-rotate).
  const renderTooltip = useCallback(() => {
    const tt = tooltipRef.current;
    if (!tt) return;
    const link = hoveredLinkRef.current;
    if (!link || !link.relations || link.relations.length === 0) {
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
    // Dedupe by (from|to|label) to avoid the tooltip ballooning when two
    // entities have many parallel edges with the same relation.
    const seen = new Set<string>();
    const unique: typeof link.relations = [];
    for (const r of link.relations) {
      const key = `${r.from}|${r.to}|${r.label || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(r);
    }
    const cap = 8;
    const shown = unique.slice(0, cap);
    const rest = unique.length - shown.length;
    tt.innerHTML = shown
      .map((r) => {
        const label = (r.label || "").replace(/_/g, " ");
        const labelHtml = label
          ? `<span class="kv-edge-tooltip-label">${escapeHtml(label)}</span>`
          : "";
        return `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-names">${escapeHtml(r.from)} → ${escapeHtml(r.to)}</span>${labelHtml}</div>`;
      })
      .join("") + (rest > 0 ? `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-label">+${rest} more</span></div>` : "");
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      mousePosRef.current = { x: e.clientX, y: e.clientY };
      if (hoveredLinkRef.current) renderTooltip();
    };
    window.addEventListener("mousemove", handler, { passive: true });
    return () => window.removeEventListener("mousemove", handler);
  }, [renderTooltip]);

  const onLinkHover = useCallback((link: object | null) => {
    hoveredLinkRef.current = (link as UnifiedLink | null) ?? null;
    renderTooltip();
  }, [renderTooltip]);

  const onNodeHover = useCallback(() => {
    hoveredLinkRef.current = null;
    if (tooltipRef.current) tooltipRef.current.style.display = "none";
  }, []);

  const onNodeClick = useCallback((raw: object) => {
    const node = raw as UnifiedNode;
    onSelect(node);
  }, [onSelect]);

  const onNodeRC = useCallback((raw: object, e: MouseEvent) => {
    e.preventDefault();
    const node = raw as UnifiedNode;
    const wrap = wrapRef.current;
    if (!wrap || !onNodeRightClick) return;
    const rect = wrap.getBoundingClientRect();
    onNodeRightClick(node, e.clientX - rect.left, e.clientY - rect.top);
  }, [onNodeRightClick]);

  // Dismiss context menu on outside click
  useEffect(() => {
    if (!contextMenu) return;
    const handler = () => onCloseContextMenu?.();
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [contextMenu, onCloseContextMenu]);

  const hasData = data.nodes.length > 0;

  return (
    <div className="ug-canvas" ref={wrapRef}>
      <ForceGraph3D
        ref={fgRef as unknown as React.MutableRefObject<undefined> | undefined}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
        controlType="orbit"
        nodeRelSize={4}
        nodeOpacity={0.9}
        nodeThreeObject={nodeThreeObject}
        linkColor={linkColor}
        linkOpacity={0.5}
        linkWidth={0.4}
        linkCurvature={0.1}
        linkDirectionalParticles={1}
        linkDirectionalParticleSpeed={0.005}
        linkDirectionalParticleWidth={1.2}
        enableNodeDrag={false}
        linkHoverPrecision={4}
        onNodeClick={onNodeClick}
        onNodeRightClick={onNodeRC}
        onBackgroundClick={() => onSelect(null)}
        onLinkHover={onLinkHover}
        onNodeHover={onNodeHover}
        onEngineStop={handleEngineStop}
      />

      <div ref={tooltipRef} className="kv-edge-tooltip" style={{ display: "none" }} />

      {!hasData && emptyState && (
        <div className="ug-empty">{emptyState}</div>
      )}

      {contextMenu && (
        <div
          className="kv-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          {contextMenu.items.map((item, i) => (
            <button
              key={i}
              className="kv-context-menu-item"
              onClick={() => { item.onClick(); onCloseContextMenu?.(); }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
});
