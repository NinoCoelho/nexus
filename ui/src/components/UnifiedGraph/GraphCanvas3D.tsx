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
import type { GraphSettings } from "./graphSettings";
import { makeGeometry, makeTextSprite, escapeHtml } from "./threeHelpers";

interface Props {
  data: UnifiedGraphData;
  selectedId: string | null;
  /** Highlight from the main "Search your knowledge" — soft white pulse. */
  search: string;
  /** Highlight from the floating /-find widget — sharper cyan pulse. */
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
  { data, selectedId, search, findQuery, settings, onSelect, onNodeRightClick, contextMenu, onCloseContextMenu, emptyState },
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

  // Pick the matched node closest to the current camera position, then
  // rotate the camera so that node ends up centered. "Closest" is the
  // intuitive notion: the user is staring at one side of the cloud; we
  // center the match they're already nearest to instead of always going
  // to the same node. Falls back to flyTo on any single match.
  const flyToNearestMatch = useCallback((nodeIds: string[]) => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition || nodeIds.length === 0) return;
    if (nodeIds.length === 1) { flyTo(nodeIds[0]); return; }
    const cam = fg.cameraPosition();
    if (!cam) { flyTo(nodeIds[0]); return; }
    const ids = new Set(nodeIds);
    let best: { node: UnifiedNode & { x?: number; y?: number; z?: number }; dist: number } | null = null;
    for (const raw of data.nodes) {
      if (!ids.has(raw.id)) continue;
      const n = raw as UnifiedNode & { x?: number; y?: number; z?: number };
      if (n.x == null || n.y == null || n.z == null) continue;
      const dx = n.x - cam.x, dy = n.y - cam.y, dz = n.z - cam.z;
      const d = dx * dx + dy * dy + dz * dz;
      if (!best || d < best.dist) best = { node: n, dist: d };
    }
    if (best) flyTo(best.node.id);
  }, [data.nodes, flyTo]);

  useImperativeHandle(ref, () => ({
    fit: callFit,
    reheat: callReheat,
    zoomIn: () => callZoom(0.7),
    zoomOut: () => callZoom(1.3),
    flyTo,
    flyToNearestMatch,
  }), [callFit, callReheat, callZoom, flyTo, flyToNearestMatch]);

  // Tune d3 forces and OrbitControls behavior. screenSpacePanning makes
  // right-click drag pan the camera in screen space (rather than world
  // space), which feels natural on 3D scatter plots. Re-applied whenever
  // the node count changes — ForceGraph3D rebuilds its simulation on
  // graphData changes, dropping previously-applied force settings.
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
  }, [data.nodes.length, settings.linkDistance, settings.chargeStrength]);

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

  const findMatchedIds = useMemo(() => {
    const term = (findQuery ?? "").trim().toLowerCase();
    if (!term) return new Set<string>();
    return new Set(data.nodes.filter((n) => n.label.toLowerCase().includes(term)).map((n) => n.id));
  }, [findQuery, data.nodes]);

  // Two pulse tracks. The main-search pulse is a soft base→white blink at
  // 700ms cycle. The /-find pulse is a sharper, brighter cyan blink at
  // ~450ms cycle so the two are visually distinct when both are active.
  // (Find takes precedence in nodeThreeObject, so a node matching both
  // shows the find pulse.)
  const pulseRef = useRef<Map<string, { mat: THREE.MeshLambertMaterial; baseColor: THREE.Color; matchColor: THREE.Color }>>(new Map());
  const pulseFindRef = useRef<Map<string, { mat: THREE.MeshLambertMaterial; baseColor: THREE.Color; matchColor: THREE.Color }>>(new Map());

  useEffect(() => {
    let rafId = 0;
    const tick = () => {
      const t = performance.now();
      const wave = (Math.sin((t / 700) * Math.PI * 2) + 1) / 2;
      const eased = Math.pow(wave, 0.6);
      pulseRef.current.forEach((entry, id) => {
        if (!matchedIds.has(id) || findMatchedIds.has(id)) return;
        const r = entry.baseColor.r + (entry.matchColor.r - entry.baseColor.r) * eased;
        const g = entry.baseColor.g + (entry.matchColor.g - entry.baseColor.g) * eased;
        const b = entry.baseColor.b + (entry.matchColor.b - entry.baseColor.b) * eased;
        entry.mat.color.setRGB(r, g, b);
        entry.mat.emissive.setRGB(r, g, b);
        entry.mat.emissiveIntensity = 0.3 + 0.8 * eased;
      });
      // Find pulse: faster (450ms), sharper (pow 0.4), brighter emissive.
      const fwave = (Math.sin((t / 450) * Math.PI * 2) + 1) / 2;
      const feased = Math.pow(fwave, 0.4);
      pulseFindRef.current.forEach((entry, id) => {
        if (!findMatchedIds.has(id)) return;
        const r = entry.baseColor.r + (entry.matchColor.r - entry.baseColor.r) * feased;
        const g = entry.baseColor.g + (entry.matchColor.g - entry.baseColor.g) * feased;
        const b = entry.baseColor.b + (entry.matchColor.b - entry.baseColor.b) * feased;
        entry.mat.color.setRGB(r, g, b);
        entry.mat.emissive.setRGB(r, g, b);
        entry.mat.emissiveIntensity = 0.5 + 1.4 * feased;
      });
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [matchedIds, findMatchedIds]);

  const nodeThreeObject = useCallback((raw: object) => {
    const node = raw as UnifiedNode;
    const isMatch = matchedIds.size > 0 && matchedIds.has(node.id);
    const isFindMatch = findMatchedIds.size > 0 && findMatchedIds.has(node.id);
    const isSelected = node.id === selectedId;
    const radius = ((1.6 + Math.log(node.degree + 1) * 0.7) + (node.radiusBoost ?? 0)) * settings.nodeSize;
    const baseColor = node.color ?? "#7a9e7e";
    const cs = getComputedStyle(document.documentElement);
    const fg = cs.getPropertyValue("--fg").trim() || "#ece8e1";
    const initial = isSelected
      ? "#ffd06a"
      : isFindMatch ? "#5cf0ff"
      : isMatch ? fg
      : baseColor;
    const emissiveIntensity = isSelected ? 0.9 : isFindMatch ? 1.2 : isMatch ? 0.7 : 0;

    const material = new THREE.MeshLambertMaterial({
      color: initial,
      emissive: initial,
      emissiveIntensity,
    });
    const mesh = new THREE.Mesh(
      makeGeometry(node.geometry, isSelected ? radius + 1 : isFindMatch ? radius + 0.5 : radius),
      material,
    );

    if (isFindMatch) {
      pulseFindRef.current.set(node.id, {
        mat: material,
        baseColor: new THREE.Color(baseColor),
        matchColor: new THREE.Color("#5cf0ff"),
      });
      pulseRef.current.delete(node.id);
    } else if (isMatch) {
      pulseRef.current.set(node.id, {
        mat: material,
        baseColor: new THREE.Color(baseColor),
        matchColor: new THREE.Color(fg),
      });
      pulseFindRef.current.delete(node.id);
    } else {
      pulseRef.current.delete(node.id);
      pulseFindRef.current.delete(node.id);
    }

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
    const sprite = makeTextSprite(label, settings.labelScale);
    sprite.position.set(0, (isSelected ? radius + 1 : radius) + 1.5, 0);
    group.add(sprite);
    return group;
  }, [matchedIds, findMatchedIds, selectedId, settings]);

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
        nodeOpacity={settings.nodeOpacity}
        nodeThreeObject={nodeThreeObject}
        linkColor={linkColor}
        linkOpacity={settings.linkOpacity}
        linkWidth={settings.linkWidth}
        linkCurvature={settings.linkCurvature}
        linkDirectionalParticles={1}
        linkDirectionalParticleSpeed={settings.particleSpeed}
        linkDirectionalParticleWidth={settings.particleWidth}
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
