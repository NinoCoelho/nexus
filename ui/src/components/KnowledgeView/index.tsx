/**
 * KnowledgeView — GraphRAG knowledge graph explorer.
 *
 * Three panels:
 *   1. Entity browser — paginated list of extracted entities with type filter
 *   2. Subgraph visualizer — Cytoscape canvas showing entity/relation graph
 *   3. Query interface — natural language search over the knowledge graph
 *
 * The view supports:
 *   - Filtering the graph to a specific file/folder (via graphSourceFilter)
 *   - Reindexing the GraphRAG engine (incremental or full)
 *   - Clicking entities/relations for detail views
 *   - Drilling down into entity subgraphs by hop count
 *
 * All data comes from the /graph/knowledge/* API endpoints backed by
 * nexus.agent.graphrag_manager and loom.store.graphrag.
 */

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  knowledgeQuery,
  getKnowledgeStats,
  getKnowledgeEntities,
  getKnowledgeEntity,
  getKnowledgeSubgraph,
  getKnowledgeFileSubgraph,
  getKnowledgeFolderSubgraph,
  type KnowledgeStats,
  type KnowledgeEntity,
  type KnowledgeQueryResult,
  type EntityDetail,
  type SubgraphData,
} from "../../api";
import VaultFilePreview from "../VaultFilePreview";
import "../KnowledgeView.css";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import { useSubgraphSimRefs } from "./useSubgraphSim";
import { SubgraphCanvas } from "./SubgraphCanvas";
const SubgraphCanvas3D = lazy(() =>
  import("./SubgraphCanvas3D").then((m) => ({ default: m.SubgraphCanvas3D })),
);
import { EntityPanel } from "./EntityPanel";
import { SourceFilterBar } from "./SourceFilterBar";
import { EntityTypeFilter } from "./EntityTypeFilter";
import { useGraphSearch } from "./useGraphSearch";

export default function KnowledgeView({
  initialSourceFilter,
  onSourceFilterHandled,
  onViewEntityGraph,
  onStartGraphIndex,
}: {
  initialSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onSourceFilterHandled?: () => void;
  onViewEntityGraph?: (path: string) => void;
  onStartGraphIndex?: (path: string) => void;
}) {
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [queryResult, setQueryResult] = useState<KnowledgeQueryResult | null>(null);
  const [topEntities, setTopEntities] = useState<KnowledgeEntity[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<EntityDetail | null>(null);
  const [pinnedEntities, setPinnedEntities] = useState<EntityDetail[]>([]);
  const [subgraphData, setSubgraphData] = useState<SubgraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [entityFilter, setEntityFilter] = useState("");
  const [splitRatio, setSplitRatio] = useState(0.5);
  const [sourceFilter, setSourceFilter] = useState<"none" | "file" | "folder">("none");
  const [sourcePath, setSourcePath] = useState("");
  const [sourceSuggestions, setSourceSuggestions] = useState<string[]>([]);
  const [showSourceSuggestions, setShowSourceSuggestions] = useState(false);
  const [graphFullscreen, setGraphFullscreen] = useState(false);
  const [viewMode, setViewMode] = useState<"2d" | "3d">(() => {
    const saved = typeof window !== "undefined" ? window.localStorage.getItem("kv:viewMode") : null;
    return saved === "3d" ? "3d" : "2d";
  });
  const toggleViewMode = useCallback(() => {
    setViewMode((prev) => {
      const next = prev === "2d" ? "3d" : "2d";
      try { window.localStorage.setItem("kv:viewMode", next); } catch { /* ignore */ }
      return next;
    });
  }, []);
  const simRefs = useSubgraphSimRefs();
  const { graphSearch, graphSearchCount, onGraphSearchChange, clearGraphSearch } = useGraphSearch(simRefs);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const splitDragRef = useRef<{ startX: number; startRatio: number } | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);

  const refreshKnowledge = useCallback(() => {
    getKnowledgeStats().then(setStats).catch(() => {});
    getKnowledgeEntities({ limit: 200 }).then((r) => setTopEntities(r.entities)).catch(() => {});
  }, []);

  useEffect(() => { refreshKnowledge(); }, [refreshKnowledge]);

  useVaultEvents((event) => {
    if (event.type !== "graphrag.indexed" && event.type !== "graphrag.removed") return;
    refreshKnowledge();
    if (sourceFilter !== "none" && sourcePath) {
      const matches =
        sourceFilter === "file"
          ? event.path === sourcePath
          : event.path.startsWith(sourcePath.endsWith("/") ? sourcePath : sourcePath + "/");
      if (matches) void applySourceFilter(sourceFilter, sourcePath);
    } else if (queryText.trim()) {
      void doSearch(queryText);
    }
  });

  useEffect(() => {
    if (!initialSourceFilter) return;
    const { mode, path } = initialSourceFilter;
    setSourceFilter(mode);
    setSourcePath(path);
    void applySourceFilter(mode, path);
    onSourceFilterHandled?.();
  }, [initialSourceFilter]);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) return;
    setLoading(true);
    setSelectedEntity(null);
    setPinnedEntities([]);
    simRefs.selectedNodeRef.current = null;
    simRefs.selectedEdgeRef.current = null;
    try {
      const result = await knowledgeQuery(q);
      setQueryResult(result);
      if (result.subgraph.nodes.length > 0) {
        setSubgraphData({
          enabled: true,
          nodes: result.subgraph.nodes.map((n) => ({ ...n, degree: n.degree ?? 0 })),
          edges: result.subgraph.edges,
        });
      }
    } catch {
      setQueryResult(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clearSearch = useCallback(() => {
    setQueryText(""); setQueryResult(null); setSubgraphData(null);
    setSelectedEntity(null); setPinnedEntities([]);
    simRefs.selectedNodeRef.current = null; simRefs.selectedEdgeRef.current = null;
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const applySourceFilter = useCallback(async (mode: "file" | "folder", path: string) => {
    if (!path.trim()) return;
    setLoading(true); setSelectedEntity(null); setPinnedEntities([]);
    simRefs.selectedNodeRef.current = null; simRefs.selectedEdgeRef.current = null;
    try {
      const sg = mode === "file" ? await getKnowledgeFileSubgraph(path) : await getKnowledgeFolderSubgraph(path);
      setSubgraphData(sg); setQueryResult(null);
    } catch { setSubgraphData(null); } finally { setLoading(false); }
  }, []);

  const clearSourceFilter = useCallback(() => {
    setSourceFilter("none"); setSourcePath(""); setSubgraphData(null); setShowSourceSuggestions(false);
  }, []);

  const onSearchChange = useCallback((value: string) => {
    setQueryText(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!value.trim()) {
      setQueryResult(null);
      setSubgraphData(null);
      return;
    }
    debounceRef.current = setTimeout(() => void doSearch(value), 300);
  }, [doSearch]);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  const selectEntity = useCallback(async (id: number) => {
    try {
      const [detail, sg] = await Promise.all([
        getKnowledgeEntity(id),
        getKnowledgeSubgraph(id, 2),
      ]);
      setSelectedEntity(detail);
      setSubgraphData(sg);
      simRefs.selectedNodeRef.current = null;
      simRefs.selectedEdgeRef.current = null;
    } catch {
      setSelectedEntity(null);
    }
  }, []);

  const pinEntity = useCallback((detail: EntityDetail) => {
    if (!detail.entity) return;
    setPinnedEntities((prev) => {
      if (prev.some((p) => p.entity?.id === detail.entity!.id)) return prev;
      return [...prev, detail];
    });
  }, []);

  const unpinEntity = useCallback((entityId: number) => {
    setPinnedEntities((prev) => prev.filter((p) => p.entity?.id !== entityId));
  }, []);

  const closeActive = useCallback(() => { setSelectedEntity(null); }, []);

  const isPinned = useCallback(
    (entityId: number) => pinnedEntities.some((p) => p.entity?.id === entityId),
    [pinnedEntities],
  );

  const hasResults = queryResult && queryResult.results.length > 0;
  const hasSubgraph = subgraphData && subgraphData.nodes.length > 0;

  return (
    <div className={`kv${graphFullscreen ? " kv--graph-fullscreen" : ""}`}>
      {!graphFullscreen && (
        <>
          <div className="kv-search-bar">
            <div className="kv-search-wrap">
              <input
                className="kv-search-input"
                type="text"
                placeholder="Search your knowledge…"
                value={queryText}
                onChange={(e) => onSearchChange(e.target.value)}
              />
              {queryText && (
                <button className="kv-search-clear" onClick={clearSearch}>&times;</button>
              )}
            </div>
            {loading && <span className="kv-search-loading">Searching…</span>}
          </div>

          <div className="kv-filters">
            <EntityTypeFilter stats={stats} typeFilter={typeFilter} onTypeFilterChange={setTypeFilter} />
            <SourceFilterBar
              sourceFilter={sourceFilter}
              sourcePath={sourcePath}
              sourceSuggestions={sourceSuggestions}
              showSourceSuggestions={showSourceSuggestions}
              onFilterModeChange={setSourceFilter}
              onPathChange={setSourcePath}
              onSuggestionsChange={setSourceSuggestions}
              onShowSuggestionsChange={setShowSourceSuggestions}
              onApply={applySourceFilter}
              onClear={clearSourceFilter}
            />
          </div>

          {stats && stats.enabled && (
            <div className="kv-stats">
              <span>{stats.entities} entities</span>
              <span className="kv-stats-dot" />
              <span>{stats.triples} relations</span>
              <span className="kv-stats-dot" />
              <span>{stats.component_count} groups</span>
            </div>
          )}
        </>
      )}

      <div className="kv-main" ref={mainRef}>
        {!graphFullscreen && (
          <div className="kv-evidence" style={{ flex: `0 0 ${splitRatio * 100}%` }}>
            <EntityPanel
            hasResults={!!hasResults}
            loading={loading}
            queryResult={queryResult}
            topEntities={topEntities}
            typeFilter={typeFilter}
            entityFilter={entityFilter}
            onEntityFilterChange={setEntityFilter}
            selectedEntity={selectedEntity}
            pinnedEntities={pinnedEntities}
            onSelectEntity={(id) => void selectEntity(id)}
            onPreviewPath={setPreviewPath}
            onPinEntity={pinEntity}
            onUnpinEntity={unpinEntity}
              onCloseSelected={closeActive}
              isPinned={isPinned}
            />
          </div>
        )}

        {!graphFullscreen && (
          <div
            className="kv-divider"
            onMouseDown={(e) => {
              e.preventDefault();
              splitDragRef.current = { startX: e.clientX, startRatio: splitRatio };
              const onMove = (ev: MouseEvent) => {
                if (!splitDragRef.current || !mainRef.current) return;
                const dx = ev.clientX - splitDragRef.current.startX;
                setSplitRatio(Math.max(0.2, Math.min(0.8, splitDragRef.current.startRatio + dx / mainRef.current.clientWidth)));
              };
              const onUp = () => { splitDragRef.current = null; window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
              window.addEventListener("mousemove", onMove);
              window.addEventListener("mouseup", onUp);
            }}
          />
        )}

        {viewMode === "3d" ? (
          <Suspense fallback={<div className="kv-graph"><div className="kv-graph-empty"><p>Loading 3D…</p></div></div>}>
          <SubgraphCanvas3D
            subgraphData={subgraphData}
            hasSubgraph={!!hasSubgraph}
            loading={loading}
            sourceFilter={sourceFilter}
            sourcePath={sourcePath}
            onStartGraphIndex={onStartGraphIndex}
            onSelectEntity={(id) => void selectEntity(id)}
            graphSearch={graphSearch}
            fullscreen={graphFullscreen}
            onToggleFullscreen={() => setGraphFullscreen((v) => !v)}
            viewMode={viewMode}
            onToggleViewMode={toggleViewMode}
          />
          </Suspense>
        ) : (
          <SubgraphCanvas
            subgraphData={subgraphData}
            graphSearch={graphSearch}
            graphSearchCount={graphSearchCount}
            hasSubgraph={!!hasSubgraph}
            loading={loading}
            sourceFilter={sourceFilter}
            sourcePath={sourcePath}
            onStartGraphIndex={onStartGraphIndex}
            refs={simRefs}
            onSelectEntity={(id) => void selectEntity(id)}
            onGraphSearchChange={onGraphSearchChange}
            onClearGraphSearch={clearGraphSearch}
            graphSearchValue={graphSearch}
            fullscreen={graphFullscreen}
            onToggleFullscreen={() => setGraphFullscreen((v) => !v)}
            viewMode={viewMode}
            onToggleViewMode={toggleViewMode}
          />
        )}
      </div>

      {previewPath && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} onViewEntityGraph={onViewEntityGraph} />
      )}
    </div>
  );
}
