/**
 * Vault mode — file-link graph for the user's vault.
 *
 * Owns scope/seed/hops/edge-types/tag-filter state and the DetailPanel
 * sidebar. Returns unified nodes/links for the shared canvas.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { getVaultGraph, getVaultEntitySources, type GraphData } from "../../../api";
import { GraphToolbar } from "../../GraphView/GraphToolbar";
import { DetailPanel } from "../../GraphView/DetailPanel";
import { EDGE_STYLES, type DetailInfo, type ScopeType } from "../../GraphView/types";
import VaultFilePreview from "../../VaultFilePreview";
import type { UnifiedGraphData, UnifiedNode } from "../types";

interface VaultModeOptions {
  onViewEntityGraph?: (path: string) => void;
}

export function useVaultMode(opts: VaultModeOptions) {
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

  const fetchGraph = useCallback(() => {
    setError(null);
    setLoading(true);
    const params = scope !== "all" && seed
      ? { scope, seed, hops, edge_types: edgeTypes }
      : { edge_types: edgeTypes };
    getVaultGraph(params)
      .then((g) => { setGraph(g); setDetail(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load graph"))
      .finally(() => setLoading(false));
  }, [scope, seed, hops, edgeTypes]);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  const filteredGraph = useMemo<GraphData | null>(() => {
    if (!graph) return null;
    if (tagFilter.size === 0) return graph;
    const visiblePaths = new Set(graph.nodes.filter(n => n.tags?.some(t => tagFilter.has(t))).map(n => n.path));
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

  const data: UnifiedGraphData = useMemo(() => {
    if (!filteredGraph) return { nodes: [], links: [] };
    const nodes: UnifiedNode[] = filteredGraph.nodes.map((n) => ({
      id: `file:${n.path}`,
      label: n.title || n.path,
      kind: "file",
      degree: 1,
      color: "#5e8a9e",
      geometry: "sphere",
      radiusBoost: Math.max(0, Math.log((n.size || 0) + 1) * 0.2),
      meta: { path: n.path },
    }));
    const seen = new Set(nodes.map(n => n.id));
    const links = filteredGraph.edges
      .map((e) => ({
        source: `file:${e.from}`,
        target: `file:${e.to}`,
        kind: e.type || "link",
        color: EDGE_STYLES[e.type || "link"]?.color || undefined,
      }))
      .filter((l) => seen.has(l.source) && seen.has(l.target));

    if (filteredGraph.entity_nodes && filteredGraph.entity_nodes.length > 0) {
      for (const ent of filteredGraph.entity_nodes) {
        const id = `entity:${ent.id}`;
        nodes.push({
          id,
          label: ent.name,
          kind: "entity",
          degree: 1,
          color: "#7a5e9e",
          geometry: "octahedron",
          meta: { entityId: ent.id, entityName: ent.name, entityType: ent.type, sourcePaths: ent.source_paths },
        });
        for (const p of ent.source_paths) {
          const fid = `file:${p}`;
          if (seen.has(fid)) {
            links.push({
              source: id,
              target: fid,
              kind: "shared-entity",
              color: EDGE_STYLES["shared-entity"]?.color || undefined,
            });
          }
        }
      }
    }
    return { nodes, links };
  }, [filteredGraph]);

  const exploreFromFile = useCallback((path: string) => {
    setScope("file");
    setSeed(path);
    setHops(1);
  }, []);

  const exploreEntity = useCallback((entityId: number) => {
    setScope("entity");
    setSeed(String(entityId));
    setHops(1);
  }, []);

  const onNodeClick = useCallback((node: UnifiedNode) => {
    const meta = node.meta as { path?: string; entityId?: number; entityName?: string; entityType?: string; sourcePaths?: string[] } | undefined;
    if (node.kind === "file" && meta?.path) {
      setDetail({ type: "file", path: meta.path });
      setPreviewPath(meta.path);
      getVaultEntitySources(meta.path).then(r => setDetailEntities(r.entities ?? [])).catch(() => setDetailEntities([]));
    } else if (node.kind === "entity" && meta?.entityId != null) {
      setDetail({
        type: "entity",
        entity: {
          id: meta.entityId,
          name: meta.entityName ?? "",
          type: meta.entityType ?? "",
          source_paths: meta.sourcePaths ?? [],
        },
      });
      setPreviewPath(null);
      setDetailEntities([]);
    }
  }, []);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    graph?.nodes.forEach(n => n.tags?.forEach(t => tags.add(t)));
    return Array.from(tags).sort();
  }, [graph]);

  const filtersBar = (
    <GraphToolbar
      scope={scope}
      seed={seed}
      hops={hops}
      edgeTypes={edgeTypes}
      loading={loading}
      nodeCount={graph?.nodes.length ?? 0}
      edgeCount={graph?.edges.length ?? 0}
      entityCount={graph?.entity_nodes?.length ?? 0}
      showFilters={showFilters}
      allTags={allTags}
      tagFilter={tagFilter}
      onScopeChange={(s) => { setScope(s); setSeed(""); }}
      onSeedChange={setSeed}
      onHopsChange={setHops}
      onEdgeTypesChange={setEdgeTypes}
      onToggleFilters={() => setShowFilters(f => !f)}
      onFitToView={() => { /* handled by canvas toolbar */ }}
      onFetchGraph={fetchGraph}
      onTagFilterChange={setTagFilter}
    />
  );

  const sidebar = (
    <>
      {error && <div className="graph-error">{error}</div>}
      {detail && (
        <DetailPanel
          detail={detail}
          graph={graph}
          detailEntities={detailEntities}
          onClose={() => { setDetail(null); setPreviewPath(null); }}
          onExploreFromFile={exploreFromFile}
          onExploreEntity={exploreEntity}
          onSetScope={(_, s) => { setScope("tag"); setSeed(s); }}
          onPreviewPath={setPreviewPath}
        />
      )}
      {previewPath && !detail && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} onViewEntityGraph={opts.onViewEntityGraph} />
      )}
    </>
  );

  return {
    data,
    sidebar,
    filtersBar,
    onNodeClick,
    contextMenu: undefined,
    refresh: fetchGraph,
    empty: null,
  };
}
