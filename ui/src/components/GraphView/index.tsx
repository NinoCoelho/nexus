/**
 * GraphView — vault file-link graph rendered on a Cytoscape canvas.
 *
 * Shows all vault .md/.mdx files as nodes and their [[wiki-links]] /
 * markdown links as edges. Supports:
 *   - Scoped queries (file, folder, tag, search, entity) via the scope selector
 *   - Entity overlay from GraphRAG (when enabled)
 *   - Node click → opens VaultFilePreview for the selected file
 *   - Entity click → shows related entities and source files
 *
 * Edge bundling and curve offsets are computed by graphEdgeUtils.ts to
 * avoid overlapping parallel edges between the same node pair.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getVaultGraph, getVaultEntitySources, type GraphData } from "../../api";
import VaultFilePreview from "../VaultFilePreview";
import "../GraphView.css";
import type { DetailInfo, ScopeType } from "./types";
import { GraphToolbar } from "./GraphToolbar";
import { DetailPanel } from "./DetailPanel";
import { draw, type DrawState } from "./drawGraph";
import { useSimulation } from "./useSimulation";
import { buildCanvasHandlers } from "./useCanvasInteraction";

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
  const [_renderTick, setRenderTick] = useState(0);

  const graphRef   = useRef<GraphData | null>(null);
  const offsetRef  = useRef({ x: 0, y: 0 });
  const scaleRef   = useRef(1);
  const hoverRef   = useRef<number | null>(null);
  const selectedRef = useRef<number | null>(null);
  const dragRef    = useRef<{ nodeIdx: number | null; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef     = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);

  function getFilteredGraph(): GraphData | null {
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

  function getDrawState(): DrawState {
    return {
      offset: offsetRef.current,
      scale: scaleRef.current,
      hover: hoverRef.current,
      selected: selectedRef.current,
      settled: settledRef.current,
    };
  }

  const { nodesRef, rafRef, runningRef, settledRef, initSim } = useSimulation(
    canvasRef,
    offsetRef,
    scaleRef,
    hoverRef,
    selectedRef,
    getFilteredGraph,
  );

  function startRAF() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    // The simulation hook manages its own RAF; expose a restart shim for interaction handler.
    runningRef.current = true;
    settledRef.current = false;
    // Re-init the loop by triggering initSim with the current graph
    const g = graphRef.current;
    if (g) initSim(g, canvasRef.current);
  }

  function handleNodeClick(hit: number) {
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

  const canvasHandlers = buildCanvasHandlers({
    canvasRef,
    nodesRef,
    runningRef,
    settledRef,
    offsetRef,
    scaleRef,
    hoverRef,
    selectedRef,
    dragRef,
    panRef,
    getFilteredGraph,
    startRAF,
    getDrawState,
    onNodeClick: handleNodeClick,
  });

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

  // Resize observer — refit canvas to parent on layout changes
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const ro = new ResizeObserver(() => {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      const g = getFilteredGraph();
      const nodes = nodesRef.current;
      if (g && nodes.length > 0) draw(canvas, g, nodes, getDrawState());
    });
    ro.observe(parent);
    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;
    return () => ro.disconnect();
  }, []);

  // Cleanup RAF on unmount
  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
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

  const allTags = (() => {
    const tags = new Set<string>();
    graph?.nodes.forEach(n => n.tags?.forEach(t => tags.add(t)));
    return Array.from(tags).sort();
  })();

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;
  const entityCount = graph?.entity_nodes?.length ?? 0;

  return (
    <div className="graph-view">
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
        onFitToView={canvasHandlers.fitToView}
        onFetchGraph={fetchGraph}
        onTagFilterChange={(tags) => { setTagFilter(tags); setRenderTick(t => t + 1); }}
      />

      {error && <div className="graph-error">{error}</div>}

      <canvas
        ref={canvasRef}
        className="graph-canvas"
        onMouseDown={canvasHandlers.onMouseDown}
        onMouseMove={canvasHandlers.onMouseMove}
        onMouseUp={canvasHandlers.onMouseUp}
        onDoubleClick={() => {
          const g = graphRef.current;
          const canvas = canvasRef.current;
          if (g && canvas) initSim(g, canvas);
        }}
        onWheel={canvasHandlers.onWheel}
      />

      {detail && (
        <DetailPanel
          detail={detail}
          graph={graph}
          detailEntities={detailEntities}
          onClose={() => { setDetail(null); setPreviewPath(null); selectedRef.current = null; }}
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
