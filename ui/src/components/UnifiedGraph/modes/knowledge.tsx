/**
 * Knowledge mode — entity/relation graph from GraphRAG.
 *
 * Owns its own data fetching and filter state. Returns unified nodes/links
 * for the shared GraphCanvas3D plus a sidebar (entity detail + pinned + query
 * results) and a toolbar popup (top entities).
 */

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
} from "../../../api";
import { useVaultEvents } from "../../../hooks/useVaultEvents";
import { TYPE_COLORS, DEFAULT_TYPE_COLOR, typeColor } from "../../KnowledgeView/typeColors";
import { EntityDetailCard } from "../../KnowledgeView/EntityDetailCard";
import { EntityTypeFilter } from "../../KnowledgeView/EntityTypeFilter";
import { SourceFilterBar } from "../../KnowledgeView/SourceFilterBar";
import { FilterChips } from "../../KnowledgeView/FilterChips";
import VaultFilePreview from "../../VaultFilePreview";
import type {
  ContextMenuItem,
  UnifiedGraphData,
  UnifiedNode,
} from "../types";

interface KnowledgeModeOptions {
  initialSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onSourceFilterHandled?: () => void;
  onViewEntityGraph?: (path: string) => void;
  onStartGraphIndex?: (path: string) => void;
  onSpawnSession?: (entityId: number, entityName: string) => void;
}

export function useKnowledgeMode(opts: KnowledgeModeOptions) {
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
  const [sourceFilter, setSourceFilter] = useState<"none" | "file" | "folder">("none");
  const [sourcePath, setSourcePath] = useState("");
  const [sourceSuggestions, setSourceSuggestions] = useState<string[]>([]);
  const [showSourceSuggestions, setShowSourceSuggestions] = useState(false);
  const [hopDepth, setHopDepth] = useState<number>(() => {
    try { return parseInt(sessionStorage.getItem("kv:hopDepth") ?? "2", 10) || 2; } catch { return 2; }
  });
  useEffect(() => { try { sessionStorage.setItem("kv:hopDepth", String(hopDepth)); } catch { /* ignore */ } }, [hopDepth]);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(() => {
    getKnowledgeStats().then(setStats).catch(() => {});
    getKnowledgeEntities({ limit: 200 }).then((r) => setTopEntities(r.entities)).catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

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

  const applySourceFilter = useCallback(async (mode: "file" | "folder", path: string) => {
    if (!path.trim()) return;
    setLoading(true); setSelectedEntity(null); setPinnedEntities([]);
    try {
      const sg = mode === "file" ? await getKnowledgeFileSubgraph(path) : await getKnowledgeFolderSubgraph(path);
      setSubgraphData(sg); setQueryResult(null);
    } catch { setSubgraphData(null); } finally { setLoading(false); }
  }, []);

  const clearSearch = useCallback(() => {
    setQueryText(""); setQueryResult(null); setSubgraphData(null);
    setSelectedEntity(null); setPinnedEntities([]);
    if (debounceRef.current) clearTimeout(debounceRef.current);
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
    if (!queryText.trim()) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => void doSearch(queryText), 300);
  }, [typeFilter]);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  // Apply incoming source filter from external trigger ("View entity graph" from vault tree)
  useEffect(() => {
    if (!opts.initialSourceFilter) return;
    const { mode, path } = opts.initialSourceFilter;
    setSourceFilter(mode);
    setSourcePath(path);
    void applySourceFilter(mode, path);
    opts.onSourceFilterHandled?.();
  }, [opts.initialSourceFilter]);

  useVaultEvents((event) => {
    if (event.type !== "graphrag.indexed" && event.type !== "graphrag.removed") return;
    refresh();
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

  const selectEntityById = useCallback(async (id: number, hops?: number) => {
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

  // Refetch subgraph when hop depth changes for the active entity
  const lastSelectedIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (lastSelectedIdRef.current !== null) {
      void selectEntityById(lastSelectedIdRef.current, hopDepth);
    }
  }, [hopDepth]);
  useEffect(() => {
    lastSelectedIdRef.current = selectedEntity?.entity?.id ?? null;
  }, [selectedEntity]);

  const pinEntity = useCallback((detail: EntityDetail) => {
    if (!detail.entity) return;
    setPinnedEntities((prev) => prev.some((p) => p.entity?.id === detail.entity!.id) ? prev : [...prev, detail]);
  }, []);
  const unpinEntity = useCallback((entityId: number) => {
    setPinnedEntities((prev) => prev.filter((p) => p.entity?.id !== entityId));
  }, []);
  const isPinned = useCallback(
    (entityId: number) => pinnedEntities.some((p) => p.entity?.id === entityId),
    [pinnedEntities],
  );

  // Type-filtered subgraph
  const filteredSubgraph = useMemo((): SubgraphData | null => {
    if (!subgraphData || !typeFilter) return subgraphData;
    const hidden = new Set(subgraphData.nodes.filter((n) => n.type !== typeFilter).map((n) => n.id));
    return {
      ...subgraphData,
      nodes: subgraphData.nodes.filter((n) => n.type === typeFilter),
      edges: subgraphData.edges.filter((e) => !hidden.has(e.source) && !hidden.has(e.target)),
    };
  }, [subgraphData, typeFilter]);

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

  const hasResults = !!filteredQueryResult && filteredQueryResult.results.length > 0;

  // Adapt to UnifiedGraphData
  const data: UnifiedGraphData = useMemo(() => {
    const sg = filteredSubgraph;
    if (!sg) return { nodes: [], links: [] };
    const nodes: UnifiedNode[] = sg.nodes.map((n) => ({
      id: `kn:${n.id}`,
      label: n.name,
      kind: n.type,
      degree: n.degree,
      color: TYPE_COLORS[n.type] ?? DEFAULT_TYPE_COLOR,
      geometry: "sphere",
      meta: { entityId: n.id, entityName: n.name, entityType: n.type },
    }));

    const groups = new Map<string, { source: string; target: string; kind: string; relations: { from: string; to: string; label: string }[] }>();
    for (const e of sg.edges) {
      const lo = Math.min(e.source, e.target);
      const hi = Math.max(e.source, e.target);
      const key = `${lo}|${hi}`;
      let g = groups.get(key);
      if (!g) {
        g = { source: `kn:${lo}`, target: `kn:${hi}`, kind: "relation", relations: [] };
        groups.set(key, g);
      }
      const fromName = sg.nodes.find((n) => n.id === e.source)?.name ?? "?";
      const toName = sg.nodes.find((n) => n.id === e.target)?.name ?? "?";
      g.relations.push({ from: fromName, to: toName, label: e.relation || "" });
    }
    return { nodes, links: Array.from(groups.values()) };
  }, [filteredSubgraph]);

  const onNodeClick = useCallback((node: UnifiedNode) => {
    const meta = node.meta as { entityId?: number } | undefined;
    if (meta?.entityId != null) void selectEntityById(meta.entityId);
  }, [selectEntityById]);

  const contextMenu = useCallback((node: UnifiedNode): ContextMenuItem[] => {
    const meta = node.meta as { entityId?: number; entityName?: string } | undefined;
    const id = meta?.entityId;
    const name = meta?.entityName ?? node.label;
    if (id == null) return [];
    return [
      { label: "Open entity", onClick: () => void selectEntityById(id) },
      { label: "Spawn chat about this", onClick: () => opts.onSpawnSession?.(id, name) },
      { label: "Copy vault link", onClick: () => navigator.clipboard.writeText(`vault://entities/${encodeURIComponent(name)}`).catch(() => {}) },
    ];
  }, [selectEntityById, opts]);

  const hasFloatingContent =
    loading || hasResults || !!selectedEntity || pinnedEntities.length > 0;

  const closeAllFloating = () => {
    setSelectedEntity(null);
    setPinnedEntities([]);
    setQueryResult(null);
    setQueryText("");
  };

  const sidebar = (
    <>
      {hasFloatingContent && (
        <div className="ug-floating-detail">
          <button
            className="ug-floating-close"
            onClick={closeAllFloating}
            title="Close panel"
            aria-label="Close panel"
          >
            ×
          </button>
          <div className="ug-floating-detail-body">
            {loading && <div className="kv-loading">Searching...</div>}
            {hasResults && filteredQueryResult && (
              <div className="kv-results">
                {filteredQueryResult.results.map((r, i) => (
                  <div key={r.chunk_id + i} className="kv-evidence-card">
                    <div className="kv-evidence-header">
                      <button className="kv-evidence-source" onClick={() => setPreviewPath(r.source_path)}>
                        {r.source_path} &rsaquo; {r.heading}
                      </button>
                      <span className={`kv-evidence-badge kv-evidence-badge--${r.source}`}>{r.source}</span>
                      <span className="kv-evidence-score">{(r.score * 100).toFixed(0)}%</span>
                    </div>
                    <p className="kv-evidence-snippet">{r.content.slice(0, 300)}</p>
                    {r.related_entities.length > 0 && (
                      <div className="kv-evidence-entities">
                        {r.related_entities.slice(0, 8).map((name) => (
                          <span key={name} className="kv-entity-tag">{name}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {(selectedEntity || pinnedEntities.length > 0) && (
              <div className="kv-cards">
                {selectedEntity && selectedEntity.entity && (() => {
                  const se = selectedEntity;
                  const seId = se.entity!.id;
                  return (
                    <EntityDetailCard
                      key={`sel-${seId}`}
                      detail={se}
                      pinned={isPinned(seId)}
                      onPin={() => pinEntity(se)}
                      onUnpin={() => unpinEntity(seId)}
                      onClose={() => setSelectedEntity(null)}
                      onSelectEntity={(id) => void selectEntityById(id)}
                      onPreview={setPreviewPath}
                    />
                  );
                })()}
                {pinnedEntities
                  .filter((p) => p.entity && p.entity.id !== selectedEntity?.entity?.id)
                  .map((p) => (
                    <EntityDetailCard
                      key={`pin-${p.entity!.id}`}
                      detail={p}
                      pinned={true}
                      onPin={() => {}}
                      onUnpin={() => unpinEntity(p.entity!.id)}
                      onClose={() => unpinEntity(p.entity!.id)}
                      onSelectEntity={(id) => void selectEntityById(id)}
                      onPreview={setPreviewPath}
                    />
                  ))}
              </div>
            )}
          </div>
        </div>
      )}

      {previewPath && (
        <VaultFilePreview path={previewPath} onClose={() => setPreviewPath(null)} onViewEntityGraph={opts.onViewEntityGraph} />
      )}
    </>
  );

  const filtersBar = (
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
  );

  // empty state for when there's no subgraph
  const empty = !data.nodes.length ? (
    sourceFilter === "file" && sourcePath ? (
      <div className="kv-graph-empty">
        <p>No entities found for this file.</p>
        {opts.onStartGraphIndex && (
          <button className="kv-index-file-btn" onClick={() => opts.onStartGraphIndex!(sourcePath)}>
            Index this file
          </button>
        )}
      </div>
    ) : (
      <div className="kv-graph-empty">
        <p className="kv-graph-empty-title">Explore your knowledge graph</p>
        <div className="kv-graph-example-queries">
          {["What am I working on?", "Who are the key people?", "Recent decisions"].map((q) => (
            <button key={q} className="kv-graph-example-btn" onClick={() => { setQueryText(q); void doSearch(q); }}>
              {q}
            </button>
          ))}
        </div>
      </div>
    )
  ) : null;

  return {
    data,
    sidebar,
    filtersBar,
    empty,
    onNodeClick,
    contextMenu,
    // top entities popup data
    topEntities,
    typeFilter,
    onPickEntity: (id: number) => void selectEntityById(id),
    // hop selector
    hopDepth,
    setHopDepth,
    // misc
    typeColor,
  };
}
