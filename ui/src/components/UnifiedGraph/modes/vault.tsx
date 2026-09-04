/**
 * Vault mode — file-link graph for the user's vault.
 *
 * Owns scope/seed/hops/edge-types/tag-filter state and the DetailPanel
 * sidebar. Returns unified nodes/links for the shared canvas.
 *
 * Seed text is deliberately two-piece: `seedInput` is what the user is
 * typing, `seed` is the committed value used in fetches. The fetch fires on
 * Go/Enter/suggestion — never per keystroke.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getVaultGraph, getVaultEntitySources, type GraphData } from "../../../api";
import { GraphToolbar } from "../../GraphView/GraphToolbar";
import { DetailPanel } from "../../GraphView/DetailPanel";
import { EDGE_STYLES, type DetailInfo, type ScopeType } from "../../GraphView/types";
import VaultFilePreview from "../../VaultFilePreview";
import type { UnifiedGraphData, UnifiedNode } from "../types";

interface VaultModeOptions {
  onViewEntityGraph?: (path: string) => void;
  /** Wire to the shared canvas handle (toolbar Fit button). */
  onFitToView?: () => void;
  /** Fetch only once the vault tab has been visited at least once. */
  enabled?: boolean;
}

export function useVaultMode(opts: VaultModeOptions) {
  const enabled = opts.enabled ?? true;
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [scope, setScope] = useState<ScopeType>("all");
  const [seedInput, setSeedInput] = useState("");
  const [seed, setSeed] = useState("");
  const [hops, setHops] = useState(2);
  const [edgeTypes, setEdgeTypes] = useState("link");
  const [maxNodes, setMaxNodes] = useState(300);
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set());
  const [showFilters, setShowFilters] = useState(false);
  const [showOrphans, setShowOrphans] = useState(false);
  const [detail, setDetail] = useState<DetailInfo | null>(null);
  const [detailEntities, setDetailEntities] = useState<{ id: number; name: string; type: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const fetchedOnceRef = useRef(false);

  // Committed query parameters. Scope/hops/edge-type changes refetch
  // immediately (deliberate dropdown choices); seed changes wait for an
  // explicit Go/Enter commit.
  const fetchGraph = useCallback(() => {
    if (!enabled) return;
    setError(null);
    setLoading(true);
    const params = scope !== "all" && seed
      ? { scope, seed, hops, edge_types: edgeTypes }
      : { edge_types: edgeTypes, max_nodes: maxNodes };
    getVaultGraph(params)
      .then((g) => { setGraph(g); setDetail(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load graph"))
      .finally(() => setLoading(false));
  }, [enabled, scope, seed, hops, edgeTypes, maxNodes]);

  useEffect(() => {
    if (!enabled) return;
    fetchGraph();
    fetchedOnceRef.current = true;
  }, [enabled, fetchGraph]);

  /** Commit the typed seed (Go / Enter / suggestion click). */
  const commitSeed = useCallback(() => {
    setSeed(seedInput.trim());
    if (scope === "all" && seedInput.trim()) {
      // typing a seed in "all" scope implicitly means file-scope exploration
      setScope("file");
    }
  }, [seedInput, scope]);

  const commitSeedPath = useCallback((path: string) => {
    setSeedInput(path);
    setSeed(path);
  }, []);

  const filteredGraph = useMemo<GraphData | null>(() => {
    if (!graph) return null;
    let nodes = graph.nodes;
    let edges = graph.edges;
    if (tagFilter.size > 0) {
      const visiblePaths = new Set(nodes.filter(n => n.tags?.some(t => tagFilter.has(t))).map(n => n.path));
      nodes = nodes.filter(n => visiblePaths.has(n.path));
      edges = edges.filter(e => visiblePaths.has(e.from) && visiblePaths.has(e.to));
    }
    if (!showOrphans) {
      const connected = new Set<string>();
      for (const e of edges) { connected.add(e.from); connected.add(e.to); }
      const kept = new Set(nodes.filter(n => connected.has(n.path)).map(n => n.path));
      // Only trim when there are actual orphans to trim — otherwise this is
      // a no-op pass that would still rebuild node identity.
      if (kept.size < nodes.length) {
        nodes = nodes.filter(n => kept.has(n.path));
      }
    }
    const connected = new Set<string>();
    for (const e of edges) { connected.add(e.from); connected.add(e.to); }
    return {
      ...graph,
      nodes,
      edges,
      orphans: nodes.filter(n => !connected.has(n.path)).map(n => n.path),
    };
  }, [graph, tagFilter, showOrphans]);

  const tagNoMatch = useMemo(
    () => tagFilter.size > 0 && (filteredGraph?.nodes.length ?? 0) === 0,
    [tagFilter.size, filteredGraph?.nodes.length],
  );

  const data: UnifiedGraphData = useMemo(() => {
    if (!filteredGraph) return { nodes: [], links: [] };
    const degree = new Map<string, number>();
    for (const n of filteredGraph.nodes) degree.set(n.path, n.degree ?? 0);
    for (const e of filteredGraph.edges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }
    const nodes: UnifiedNode[] = filteredGraph.nodes.map((n) => ({
      id: `file:${n.path}`,
      label: n.title || n.path,
      kind: "file",
      degree: degree.get(n.path) ?? 1,
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
          degree: ent.source_paths.length,
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
    commitSeedPath(path);
    setHops(2);
  }, [commitSeedPath]);

  const exploreEntity = useCallback((entityId: number) => {
    setScope("entity");
    commitSeedPath(String(entityId));
    setHops(2);
  }, [commitSeedPath]);

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

  const orphanCount = graph?.orphans.length ?? 0;

  const filtersBar = (
    <>
      <GraphToolbar
        scope={scope}
        seed={seedInput}
        hops={hops}
        edgeTypes={edgeTypes}
        loading={loading}
        nodeCount={graph?.nodes.length ?? 0}
        edgeCount={graph?.edges.length ?? 0}
        entityCount={graph?.entity_nodes?.length ?? 0}
        showFilters={showFilters}
        allTags={allTags}
        tagFilter={tagFilter}
        onScopeChange={(s) => { setScope(s); setSeedInput(""); setSeed(""); }}
        onSeedChange={setSeedInput}
        onHopsChange={setHops}
        onEdgeTypesChange={setEdgeTypes}
        onToggleFilters={() => setShowFilters(f => !f)}
        onFitToView={() => opts.onFitToView?.()}
        onFetchGraph={commitSeed}
        onTagFilterChange={setTagFilter}
      />
      <div className="graph-toolbar-secondary">
        {graph?.capped && (
          <span className="graph-cap-note">
            Showing {graph.nodes.length} of {graph.total_nodes} most-connected notes
            <button className="graph-cap-more" onClick={() => setMaxNodes(2000)}>Show all</button>
          </span>
        )}
        {orphanCount > 0 && (
          <button
            className={`graph-orphan-toggle${showOrphans ? " active" : ""}`}
            onClick={() => setShowOrphans(v => !v)}
            title={`${orphanCount} notes with no links — click to ${showOrphans ? "hide" : "show"} them`}
          >
            {showOrphans ? "Hide" : "Show"} {orphanCount} orphans
          </button>
        )}
      </div>
    </>
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
          onSetScope={(_, s) => { setScope("tag"); commitSeedPath(s); }}
          onPreviewPath={setPreviewPath}
        />
      )}
      {previewPath && !detail && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} onViewEntityGraph={opts.onViewEntityGraph} />
      )}
    </>
  );

  const empty = loading && !graph
    ? <div className="kv-graph-empty"><p>Loading graph…</p></div>
    : tagNoMatch
    ? (
      <div className="kv-graph-empty">
        <p>No notes match the selected tags.</p>
        <button className="kv-index-file-btn" onClick={() => setTagFilter(new Set())}>Clear tag filter</button>
      </div>
    )
    : error && !graph
    ? <div className="kv-graph-empty"><p>{error}</p></div>
    : null;

  return {
    data,
    sidebar,
    filtersBar,
    onNodeClick,
    contextMenu: undefined,
    refresh: fetchGraph,
    empty,
  };
}
