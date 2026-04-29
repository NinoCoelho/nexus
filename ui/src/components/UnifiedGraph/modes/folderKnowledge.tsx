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
  type FolderSubgraphData,
} from "../../../api/folderGraph";
import { TYPE_COLORS, DEFAULT_TYPE_COLOR } from "../../KnowledgeView/typeColors";
import type { ContextMenuItem, UnifiedGraphData, UnifiedNode } from "../types";

interface FolderKnowledgeOptions {
  /** Vault-relative folder path. */
  path: string;
  /** Bumped externally (e.g. after reindex) to force a refetch. */
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
  /** Number of nodes returned. Lets the host show a quick header stat. */
  nodeCount: number;
}

export function useFolderKnowledgeMode(opts: FolderKnowledgeOptions): FolderKnowledgeHook {
  const { path, refreshKey = 0 } = opts;
  const [sg, setSg] = useState<FolderSubgraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const data: UnifiedGraphData = useMemo(() => {
    if (!sg) return { nodes: [], links: [] };
    const N = sg.nodes.length;
    const radius = Math.max(40, Math.cbrt(N) * 18);
    const nodes: (UnifiedNode & { x?: number; y?: number; z?: number })[] = sg.nodes.map(
      (n, i) => {
        const phi = Math.acos(1 - (2 * (i + 0.5)) / Math.max(1, N));
        const theta = Math.PI * (1 + Math.sqrt(5)) * i;
        return {
          id: `fk:${n.id}`,
          label: n.name,
          kind: n.type,
          degree: n.degree,
          color: TYPE_COLORS[n.type] ?? DEFAULT_TYPE_COLOR,
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
  }, [sg]);

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
    filtersBar: null,
    sidebar: null,
    onNodeClick,
    refresh,
    loading,
    nodeCount: data.nodes.length,
    empty,
  };
}
