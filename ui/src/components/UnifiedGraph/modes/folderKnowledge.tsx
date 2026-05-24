/**
 * Folder-knowledge mode hook — per-folder, ontology-isolated graph.
 *
 * Distinct from the global knowledge mode: data comes from /graph/folder/*
 * and is scoped to a single folder's `.nexus-graph/` index. The hook owns
 * its own loading state, sub-graph fetch, and a refresh entry-point used
 * after a reindex completes.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getFolderFullSubgraph,
  type FolderOntology,
  type FolderSubgraphData,
} from "../../../api/folderGraph";
import { TYPE_COLORS, DEFAULT_TYPE_COLOR } from "../../KnowledgeView/typeColors";
import type { ContextMenuItem, UnifiedGraphData, UnifiedNode } from "../types";

interface FolderKnowledgeOptions {
  path: string;
  refreshKey?: number;
}

export interface FolderKnowledgeHook {
  data: UnifiedGraphData;
  filtersBar: React.ReactNode;
  sidebar: React.ReactNode;
  onNodeClick: (node: UnifiedNode) => void;
  contextMenu?: (node: UnifiedNode) => ContextMenuItem[];
  empty?: React.ReactNode;
  refresh: () => void;
  loading: boolean;
  nodeCount: number;
}

function typeColorForFolder(t: string, ontology?: FolderOntology): string {
  if (TYPE_COLORS[t]) return TYPE_COLORS[t];
  const types = ontology?.entity_types ?? [];
  const idx = types.indexOf(t);
  if (idx >= 0) {
    const palette = [
      "#c9a84c", "#b87333", "#7a5e9e", "#5e7a9e", "#9e4a3a",
      "#4a9e7a", "#9e7a4a", "#4a7a9e", "#7a9e4a", "#9e4a7a",
    ];
    return palette[idx % palette.length];
  }
  return DEFAULT_TYPE_COLOR;
}

export function useFolderKnowledgeMode(opts: FolderKnowledgeOptions): FolderKnowledgeHook {
  const { path, refreshKey = 0 } = opts;
  const [sg, setSg] = useState<FolderSubgraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getFolderFullSubgraph(path);
      setSg(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSg(null);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshKey]);

  const typeCounts = useMemo(() => {
    if (!sg) return {} as Record<string, number>;
    const counts: Record<string, number> = {};
    for (const n of sg.nodes) {
      counts[n.type] = (counts[n.type] || 0) + 1;
    }
    return counts;
  }, [sg]);

  const allTypes = useMemo(() => {
    const ontologyTypes = sg?.ontology?.entity_types ?? [];
    const actualTypes = Object.keys(typeCounts);
    const merged = new Set([...ontologyTypes, ...actualTypes]);
    return Array.from(merged);
  }, [sg?.ontology, typeCounts]);

  const data: UnifiedGraphData = useMemo(() => {
    if (!sg) return { nodes: [], links: [] };
    const ontology = sg.ontology;
    let filtered = sg.nodes;
    if (typeFilter !== null) {
      filtered = sg.nodes.filter((n) => n.type === typeFilter);
    }
    const N = filtered.length;
    const radius = Math.max(40, Math.cbrt(N) * 18);
    const filteredIds = new Set(filtered.map((n) => n.id));
    const nodes: (UnifiedNode & { x?: number; y?: number; z?: number })[] = filtered.map(
      (n, i) => {
        const phi = Math.acos(1 - (2 * (i + 0.5)) / Math.max(1, N));
        const theta = Math.PI * (1 + Math.sqrt(5)) * i;
        return {
          id: `fk:${n.id}`,
          label: n.name,
          kind: n.type,
          degree: n.degree,
          color: typeColorForFolder(n.type, ontology),
          geometry: "sphere",
          meta: { entityId: n.id, entityName: n.name, entityType: n.type },
          x: radius * Math.sin(phi) * Math.cos(theta),
          y: radius * Math.sin(phi) * Math.sin(theta),
          z: radius * Math.cos(phi),
        };
      },
    );

    const groups = new Map<
      string,
      {
        source: string;
        target: string;
        kind: string;
        relations: { from: string; to: string; label: string }[];
      }
    >();
    for (const e of sg.edges) {
      if (!filteredIds.has(e.source) || !filteredIds.has(e.target)) continue;
      const lo = Math.min(e.source, e.target);
      const hi = Math.max(e.source, e.target);
      const key = `${lo}|${hi}`;
      let g = groups.get(key);
      if (!g) {
        g = { source: `fk:${lo}`, target: `fk:${hi}`, kind: "relation", relations: [] };
        groups.set(key, g);
      }
      const fromName = sg.nodes.find((n) => n.id === e.source)?.name ?? "?";
      const toName = sg.nodes.find((n) => n.id === e.target)?.name ?? "?";
      g.relations.push({ from: fromName, to: toName, label: e.relation || "" });
    }
    return { nodes, links: Array.from(groups.values()) };
  }, [sg, typeFilter]);

  const filtersBar = useMemo(() => {
    if (allTypes.length === 0) return null;
    return (
      <div className="kv-filters">
        <button
          className={`kv-pill${typeFilter === null ? " kv-pill--active" : ""}`}
          onClick={() => setTypeFilter(null)}
        >
          All
        </button>
        {allTypes.map((t) => (
          <button
            key={t}
            className={`kv-pill${typeFilter === t ? " kv-pill--active" : ""}`}
            style={{ "--pill-color": typeColorForFolder(t, sg?.ontology) } as React.CSSProperties}
            onClick={() => setTypeFilter(typeFilter === t ? null : t)}
          >
            <span
              className="kv-pill-swatch"
              style={{ background: typeColorForFolder(t, sg?.ontology) }}
            />
            {t} <span className="kv-pill-count">{typeCounts[t] ?? 0}</span>
          </button>
        ))}
      </div>
    );
  }, [allTypes, typeFilter, typeCounts, sg?.ontology]);

  const onNodeClick = useCallback(() => {
    /* v1: clicks are visual only — no entity-detail panel for folder graphs yet. */
  }, []);

  const empty = !loading && data.nodes.length === 0 ? (
    <div className="ug-empty">
      {error ? (
        <>
          <div>Could not load graph</div>
          <div style={{ opacity: 0.7, fontSize: 12, marginTop: 8 }}>{error}</div>
        </>
      ) : (
        <>
          <div>No entities indexed yet</div>
          <div style={{ opacity: 0.7, fontSize: 12, marginTop: 8 }}>
            Click <b>Reindex</b> in the toolbar to build the graph.
          </div>
        </>
      )}
    </div>
  ) : undefined;

  return {
    data,
    filtersBar,
    sidebar: null,
    onNodeClick,
    refresh,
    loading,
    nodeCount: data.nodes.length,
    empty,
  };
}
