/**
 * GraphCanvas3D — the WebGL 3D renderer (optional; 2D is the default).
 *
 * Sandboxed webviews (VS Code, Electron) cap concurrent WebGL contexts at one
 * and forceContextLoss is async, so unmounting/remounting per tab races the
 * GPU and the new context fails. This component is mounted once by
 * UnifiedGraph/index.tsx and stays mounted across mode switches; only its
 * `graphData` and callback props change.
 *
 * THREE objects are tracked per node id and disposed when a node's object is
 * rebuilt or when the node leaves the dataset — react-force-graph never
 * disposes them itself, so without this every selection click would leak a
 * full set of geometries + materials + label textures.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef } from "react";
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
import { makeGeometry, makeTextSprite } from "./threeHelpers";
import { GraphContextMenu, useLinkTooltip, usePendingFit } from "./canvasCommon";

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
  renderer?: () => { domElement?: HTMLCanvasElement } | undefined;
};

/** Dispose every GPU resource held by a node's THREE group. */
function disposeGroup(group: THREE.Object3D): void {
  group.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const mat = (mesh as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) {
      for (const m of mat) {
        const anyMat = m as THREE.MeshLambertMaterial & { map?: THREE.Texture };
        anyMat.map?.dispose();
        m.dispose();
      }
    } else if (mat) {
      const anyMat = mat as THREE.MeshLambertMaterial & { map?: THREE.Texture };
      anyMat.map?.dispose();
      mat.dispose();
    }
  });
}

export const GraphCanvas3D = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas3D(
  { data, selectedId, search, findQuery, settings, onSelect, onNodeRightClick, contextMenu, onCloseContextMenu, emptyState },
  ref,
) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<FgInstance | null>(null);

  const tooltip = useLinkTooltip();
  const pendingFitRef = usePendingFit(data);

  const callFit = useCallback(() => fgRef.current?.zoomToFit?.(600, 60), []);
  const callReheat = useCallback(() => fgRef.current?.d3ReheatSimulation?.(), []);

  const callZoom = useCallback((factor: number) => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition) return;
    const pos = fg.cameraPosition();
    if (!pos) return;
    const next = { x: pos.x * factor, y: pos.y * factor, z: pos.z * factor };
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
  // rotate the camera so that node ends up centered.
  const flyToNearestMatch = useCallback((nodeIds: string[]) => {
    const fg = fgRef.current;
    if (!fg?.cameraPosition || nodeIds.length === 0) return;
    if (nodeIds.length === 1) { flyTo(nodeIds[0]); return; }
    const cam = fg.cameraPosition();
    if (!cam) { flyTo(nodeIds[0]); return; }
    const ids = new Set(nodeIds);
    let best: { id: string; dist: number } | null = null;
    for (const raw of data.nodes) {
      if (!ids.has(raw.id)) continue;
      const n = raw as UnifiedNode & { x?: number; y?: number; z?: number };
      if (n.x == null || n.y == null || n.z == null) continue;
      const dx = n.x - cam.x, dy = n.y - cam.y, dz = n.z - cam.z;
      const d = dx * dx + dy * dy + dz * dz;
      if (!best || d < best.dist) best = { id: n.id, dist: d };
    }
    if (best) flyTo(best.id);
  }, [data.nodes, flyTo]);

  useImperativeHandle(ref, () => ({
    fit: callFit,
    reheat: callReheat,
    zoomIn: () => callZoom(0.7),
    zoomOut: () => callZoom(1.3),
    flyTo,
    flyToNearestMatch,
  }), [callFit, callReheat, callZoom, flyTo, flyToNearestMatch]);

  // Release the WebGL context on final unmount. Sandbox webviews cap
  // contexts at one, and the next mount's probe fails if we leave it
  // dangling for the GPU driver to reap.
  useEffect(() => {
    return () => {
      try {
        const canvas = fgRef.current?.renderer?.()?.domElement;
        canvas?.getContext("webgl2")?.getExtension("WEBGL_lose_context")?.loseContext();
      } catch { /* best-effort */ }
    };
  }, []);

  // Tune d3 forces and OrbitControls behavior. Re-applied whenever the node
  // count changes — ForceGraph3D rebuilds its simulation on graphData
  // changes, dropping previously-applied force settings.
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

  const handleEngineStop = useCallback(() => {
    if (!pendingFitRef.current) return;
    pendingFitRef.current = false;
    callFit();
  }, [callFit, pendingFitRef]);

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

  // Two pulse tracks: main-search (soft white, 700ms) and /-find (sharp
  // cyan, 450ms). Find takes precedence in nodeThreeObject.
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

  // Live THREE objects per node id — rebuilt objects and vanished nodes are
  // disposed so repeated selections / mode switches don't leak GPU memory.
  const liveObjectsRef = useRef<Map<string, THREE.Group>>(new Map());

  useEffect(() => {
    const live = liveObjectsRef.current;
    const alive = new Set(data.nodes.map((n) => n.id));
    for (const [id, group] of live) {
      if (!alive.has(id)) {
        disposeGroup(group);
        live.delete(id);
        pulseRef.current.delete(id);
        pulseFindRef.current.delete(id);
      }
    }
  }, [data]);

  // Theme colors read once per mount instead of per node (forced style
  // reads inside nodeThreeObject were a jank source on large graphs).
  const themeRef = useRef({ fg: "#ece8e1" });
  useEffect(() => {
    try {
      const cs = getComputedStyle(document.documentElement);
      themeRef.current.fg = cs.getPropertyValue("--fg").trim() || "#ece8e1";
    } catch { /* ignore */ }
  }, []);

  const nodeThreeObject = useCallback((raw: object) => {
    const node = raw as UnifiedNode;
    const isMatch = matchedIds.size > 0 && matchedIds.has(node.id);
    const isFindMatch = findMatchedIds.size > 0 && findMatchedIds.has(node.id);
    const isSelected = node.id === selectedId;
    const radius = ((1.6 + Math.log(node.degree + 1) * 0.7) + (node.radiusBoost ?? 0)) * settings.nodeSize;
    const baseColor = node.color ?? "#7a9e7e";
    const fg = themeRef.current.fg;
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

    // Dispose the previous object for this node (selection/search changes
    // rebuild every node's object).
    const prev = liveObjectsRef.current.get(node.id);
    if (prev) disposeGroup(prev);
    liveObjectsRef.current.set(node.id, group);
    return group;
  }, [matchedIds, findMatchedIds, selectedId, settings]);

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
      <ForceGraph3D
        ref={fgRef as unknown as React.MutableRefObject<undefined> | undefined}
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
        linkDirectionalParticles={data.links.length <= 400 ? 1 : 0}
        linkDirectionalParticleSpeed={settings.particleSpeed}
        linkDirectionalParticleWidth={settings.particleWidth}
        enableNodeDrag={false}
        linkHoverPrecision={4}
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
