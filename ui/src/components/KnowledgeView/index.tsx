import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { SubgraphCanvas3D } from "./SubgraphCanvas3D";
import { EntityPanel } from "./EntityPanel";
import { SourceFilterBar } from "./SourceFilterBar";
import { EntityTypeFilter } from "./EntityTypeFilter";
import { FilterChips } from "./FilterChips";

export default function KnowledgeView({
  initialSourceFilter,
  onSourceFilterHandled,
  onViewEntityGraph,
  onStartGraphIndex,
  onSpawnSession,
}: {
  initialSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onSourceFilterHandled?: () => void;
  onViewEntityGraph?: (path: string) => void;
  onStartGraphIndex?: (path: string) => void;
  onSpawnSession?: (entityId: number, entityName: string) => void;
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
  const [graphSearch, setGraphSearch] = useState("");
  const [hopDepth, setHopDepth] = useState<number>(() => {
    try { return parseInt(sessionStorage.getItem("kv:hopDepth") ?? "2", 10) || 2; } catch { return 2; }
  });
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
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const applySourceFilter = useCallback(async (mode: "file" | "folder", path: string) => {
    if (!path.trim()) return;
    setLoading(true); setSelectedEntity(null); setPinnedEntities([]);
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

  // Re-trigger search when typeFilter changes if there's an active query
  useEffect(() => {
    if (!queryText.trim()) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => void doSearch(queryText), 300);
  }, [typeFilter]);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  const selectEntity = useCallback(async (id: number, hops?: number) => {
    try {
      const [detail, sg] = await Promise.all([
        getKnowledgeEntity(id),
        getKnowledgeSubgraph(id, hops ?? hopDepth),
      ]);
      setSelectedEntity(detail);
      setSubgraphData(sg);
    } catch {
      setSelectedEntity(null);
    }
  }, [hopDepth]);

  // When hopDepth changes and a node is selected, refetch its subgraph
  const lastSelectedIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (lastSelectedIdRef.current !== null) {
      void selectEntity(lastSelectedIdRef.current, hopDepth);
    }
  }, [hopDepth]);

  // Track selected entity id for hop refetch
  useEffect(() => {
    lastSelectedIdRef.current = selectedEntity?.entity?.id ?? null;
  }, [selectedEntity]);

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

  // Client-side typeFilter applied to subgraph
  const filteredSubgraphData = useMemo((): SubgraphData | null => {
    if (!subgraphData || !typeFilter) return subgraphData;
    const hiddenIds = new Set(
      subgraphData.nodes.filter((n) => n.type !== typeFilter).map((n) => n.id),
    );
    const visibleNodes = subgraphData.nodes.filter((n) => n.type === typeFilter);
    const visibleEdges = subgraphData.edges.filter(
      (e) => !hiddenIds.has(e.source) && !hiddenIds.has(e.target),
    );
    return { ...subgraphData, nodes: visibleNodes, edges: visibleEdges };
  }, [subgraphData, typeFilter]);

  // Client-side typeFilter on queryResult
  const filteredQueryResult = useMemo((): KnowledgeQueryResult | null => {
    if (!queryResult || !typeFilter) return queryResult;
    const filtered = queryResult.results.filter(
      (r) => !r.related_entities || r.related_entities.length === 0 ||
        r.related_entities.some((name) =>
          topEntities.some((e) => e.name === name && e.type === typeFilter),
        ),
    );
    return { ...queryResult, results: filtered };
  }, [queryResult, typeFilter, topEntities]);

  const hasResults = filteredQueryResult && filteredQueryResult.results.length > 0;
  const hasSubgraph = filteredSubgraphData && filteredSubgraphData.nodes.length > 0;

  const onExampleQuery = useCallback((q: string) => {
    setQueryText(q);
    void doSearch(q);
  }, [doSearch]);

  const handleHopDepthChange = useCallback((h: number) => {
    setHopDepth(h);
  }, []);

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

          <FilterChips
            typeFilter={typeFilter}
            sourceFilter={sourceFilter}
            sourcePath={sourcePath}
            queryText={queryText}
            onClearType={() => setTypeFilter(null)}
            onClearSource={clearSourceFilter}
            onClearQuery={clearSearch}
          />

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
              queryResult={filteredQueryResult}
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
              const onUp = () => {
                splitDragRef.current = null;
                window.removeEventListener("mousemove", onMove);
                window.removeEventListener("mouseup", onUp);
              };
              window.addEventListener("mousemove", onMove);
              window.addEventListener("mouseup", onUp);
            }}
          />
        )}

        <SubgraphCanvas3D
          subgraphData={filteredSubgraphData}
          hasSubgraph={!!hasSubgraph}
          loading={loading}
          sourceFilter={sourceFilter}
          sourcePath={sourcePath}
          onStartGraphIndex={onStartGraphIndex}
          onSelectEntity={(id) => void selectEntity(id)}
          graphSearch={graphSearch}
          onGraphSearchChange={setGraphSearch}
          fullscreen={graphFullscreen}
          onToggleFullscreen={() => setGraphFullscreen((v) => !v)}
          hopDepth={hopDepth}
          onHopDepthChange={handleHopDepthChange}
          onExampleQuery={onExampleQuery}
          onSpawnSession={onSpawnSession}
        />
      </div>

      {previewPath && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} onViewEntityGraph={onViewEntityGraph} />
      )}
    </div>
  );
}
