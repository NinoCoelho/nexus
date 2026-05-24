/**
 * GraphView — vault file-link graph rendered in 3D with react-force-graph-3d.
 *
 * Shows all vault .md/.mdx files as nodes and their [[wiki-links]] /
 * markdown links as edges. Supports:
 *   - Scoped queries (file, folder, tag, search, entity) via the scope selector
 *   - Entity overlay from GraphRAG (when enabled)
 *   - Node click → opens VaultFilePreview for the selected file
 *   - Entity click → shows related entities and source files
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import { getVaultGraph, getVaultEntitySources, type GraphData } from "../../api";
import VaultFilePreview from "../VaultFilePreview";
import "../GraphView.css";
import type { DetailInfo, ScopeType } from "./types";
import { EDGE_STYLES } from "./types";
import { GraphToolbar } from "./GraphToolbar";
import { DetailPanel } from "./DetailPanel";

type FgInstance = {
  zoomToFit?: (ms?: number, padding?: number) => void;
  d3ReheatSimulation?: () => void;
  d3Force?: (kind: string) => { distance?: (n: number) => unknown; strength?: (n: number) => unknown } | undefined;
  pauseAnimation?: () => void;
  renderer?: () => { forceContextLoss?: () => void; dispose?: () => void } | undefined;
  scene?: () => { clear?: () => void } | undefined;
};

interface Node3D {
  id: string;
  label: string;
  nodeType: "file" | "entity";
  path?: string;
  entityId?: number;
  entityName?: string;
  entityType?: string;
  size: number;
}

interface Link3D {
  source: string;
  target: string;
  type: string;
}

export default function GraphView({ onViewEntityGraph }: { onViewEntityGraph?: (path: string) => void }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<FgInstance | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 600 });
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
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Dispose the WebGL context on unmount. Sandboxed webviews (VS Code,
  // Electron) cap concurrent WebGL contexts; without explicit cleanup the
  // context lingers and the next 3D view fails to allocate a new one.
  useEffect(() => {
    return () => {
      const fg = fgRef.current;
      try {
        fg?.pauseAnimation?.();
        fg?.scene?.()?.clear?.();
        const r = fg?.renderer?.();
        r?.forceContextLoss?.();
        r?.dispose?.();
      } catch { /* ignore */ }
    };
  }, []);

  const filteredGraph = useMemo<GraphData | null>(() => {
    if (!graph) return null;
    if (tagFilter.size === 0) return graph;
    const visiblePaths = new Set(
      graph.nodes.filter(n => n.tags?.some(t => tagFilter.has(t))).map(n => n.path)
    );
    if (visiblePaths.size === 0) return graph;
    const filteredNodes = graph.nodes.filter(n => visiblePaths.has(n.path));
    const filteredEdges = graph.edges.filter(e => visiblePaths.has(e.from) && visiblePaths.has(e.to));
    const connected = new Set<string>();
    for (const e of filteredEdges) { connected.add(e.from); connected.add(e.to); }
    return {
      ...graph,
      nodes: filteredNodes,
      edges: filteredEdges,
      orphans: filteredNodes.filter(n => !connected.has(n.path)).map(n => n.path),
    };
  }, [graph, tagFilter]);

  const data = useMemo<{ nodes: Node3D[]; links: Link3D[] }>(() => {
    if (!filteredGraph) return { nodes: [], links: [] };
    const nodes: Node3D[] = filteredGraph.nodes.map((n) => ({
      id: `file:${n.path}`,
      label: n.title || n.path,
      nodeType: "file",
      path: n.path,
      size: Math.max(2, Math.log((n.size || 0) + 1)),
    }));
    const seen = new Set(nodes.map(n => n.id));
    const links: Link3D[] = filteredGraph.edges
      .map((e) => ({ source: `file:${e.from}`, target: `file:${e.to}`, type: e.type || "link" }))
      .filter((l) => seen.has(l.source) && seen.has(l.target));

    if (filteredGraph.entity_nodes && filteredGraph.entity_nodes.length > 0) {
      for (const ent of filteredGraph.entity_nodes) {
        const id = `entity:${ent.id}`;
        nodes.push({
          id,
          label: ent.name,
          nodeType: "entity",
          entityId: ent.id,
          entityName: ent.name,
          entityType: ent.type,
          size: 3,
        });
        for (const p of ent.source_paths) {
          const fid = `file:${p}`;
          if (seen.has(fid)) links.push({ source: id, target: fid, type: "shared-entity" });
        }
      }
    }
    return { nodes, links };
  }, [filteredGraph]);

  const fetchGraph = useCallback(() => {
    setError(null);
    setLoading(true);
    const params = scope !== "all" && seed
      ? { scope, seed, hops, edge_types: edgeTypes }
      : { edge_types: edgeTypes };
    getVaultGraph(params)
      .then((g) => {
        setGraph(g);
        setDetail(null);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load graph");
      })
      .finally(() => setLoading(false));
  }, [scope, seed, hops, edgeTypes]);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  const fitToView = useCallback(() => {
    fgRef.current?.zoomToFit?.(600, 60);
  }, []);

  useEffect(() => {
    if (!data.nodes.length) return;
    const t = setTimeout(fitToView, 1200);
    return () => clearTimeout(t);
  }, [data.nodes.length, fitToView]);

  const nodeThreeObject = useCallback((raw: object) => {
    const node = raw as Node3D;
    const isSelected = node.id === selectedId;
    const baseColor = node.nodeType === "entity" ? "#7a5e9e" : "#5e8a9e";
    const color = isSelected ? "#ffd06a" : baseColor;
    const radius = node.size + (isSelected ? 1 : 0);
    const geom = node.nodeType === "entity"
      ? new THREE.OctahedronGeometry(radius)
      : new THREE.SphereGeometry(radius, 16, 16);
    const sphere = new THREE.Mesh(
      geom,
      new THREE.MeshLambertMaterial({
        color,
        emissive: color,
        emissiveIntensity: isSelected ? 0.8 : 0,
      }),
    );
    const group = new THREE.Group();
    group.add(sphere);
    const label = node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label;
    const sprite = makeTextSprite(label);
    sprite.position.set(0, radius + 1.5, 0);
    group.add(sprite);
    return group;
  }, [selectedId]);

  const linkColor = useCallback((raw: object) => {
    const l = raw as Link3D;
    const style = EDGE_STYLES[l.type];
    return style?.color || "rgba(180,180,180,0.45)";
  }, []);

  const onNodeClick = useCallback((raw: object) => {
    const n = raw as Node3D;
    setSelectedId(n.id);
    if (n.nodeType === "file" && n.path) {
      setDetail({ type: "file", path: n.path });
      setPreviewPath(n.path);
      getVaultEntitySources(n.path).then(r => setDetailEntities(r.entities ?? [])).catch(() => setDetailEntities([]));
    } else if (n.nodeType === "entity" && n.entityId != null) {
      setDetail({
        type: "entity",
        entity: { id: n.entityId, name: n.entityName || "", type: n.entityType || "", source_paths: [] },
      });
      setPreviewPath(null);
      setDetailEntities([]);
    }
  }, []);

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

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    graph?.nodes.forEach(n => n.tags?.forEach(t => tags.add(t)));
    return Array.from(tags).sort();
  }, [graph]);

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;
  const entityCount = graph?.entity_nodes?.length ?? 0;

  return (
    <div className="graph-view" ref={wrapRef}>
      <GraphToolbar
        scope={scope}
        seed={seed}
        hops={hops}
        edgeTypes={edgeTypes}
        loading={loading}
        nodeCount={nodeCount}
        edgeCount={edgeCount}
        entityCount={entityCount}
        showFilters={showFilters}
        allTags={allTags}
        tagFilter={tagFilter}
        onScopeChange={(s) => { setScope(s); setSeed(""); }}
        onSeedChange={setSeed}
        onHopsChange={setHops}
        onEdgeTypesChange={setEdgeTypes}
        onToggleFilters={() => setShowFilters(f => !f)}
        onFitToView={fitToView}
        onFetchGraph={fetchGraph}
        onTagFilterChange={setTagFilter}
      />

      {error && <div className="graph-error">{error}</div>}

      <ForceGraph3D
        ref={fgRef as unknown as React.MutableRefObject<undefined> | undefined}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
        nodeThreeObject={nodeThreeObject}
        linkColor={linkColor}
        linkOpacity={0.5}
        linkWidth={0.4}
        linkCurvature={0.1}
        linkDirectionalParticles={1}
        linkDirectionalParticleSpeed={0.005}
        enableNodeDrag
        onNodeClick={onNodeClick}
        onBackgroundClick={() => setSelectedId(null)}
        onEngineStop={fitToView}
      />

      {detail && (
        <DetailPanel
          detail={detail}
          graph={graph}
          detailEntities={detailEntities}
          onClose={() => { setDetail(null); setPreviewPath(null); setSelectedId(null); }}
          onExploreFromFile={exploreFrom}
          onExploreEntity={exploreEntity}
          onSetScope={(_, s) => { setScope("tag"); setSeed(s); }}
          onPreviewPath={setPreviewPath}
        />
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

function makeTextSprite(text: string): THREE.Sprite {
  const padding = 6;
  const fontSize = 22;
  const measure = document.createElement("canvas").getContext("2d")!;
  measure.font = `${fontSize}px system-ui, sans-serif`;
  const textWidth = measure.measureText(text).width;
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(textWidth + padding * 2);
  canvas.height = fontSize + padding * 2;
  const ctx = canvas.getContext("2d")!;
  const cs = getComputedStyle(document.documentElement);
  const fg = cs.getPropertyValue("--fg").trim() || "#ece8e1";
  const bgPanel = cs.getPropertyValue("--bg-panel").trim() || "rgba(29, 32, 37, 0.85)";
  ctx.font = `${fontSize}px system-ui, sans-serif`;
  ctx.fillStyle = bgPanel;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = fg;
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
