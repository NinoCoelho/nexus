/**
 * Agent mode — agent/skill/session graph for the Nexus runtime.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { getAgentGraph, type AgentGraphData, type AgentGraphNode } from "../../../api";
import type { GeometryKind, UnifiedGraphData, UnifiedNode } from "../types";

const TYPE_COLOR: Record<AgentGraphNode["type"], string> = {
  agent: "#c9a84c",
  skill: "#5e8a9e",
  session: "#7a5e9e",
};

const TYPE_GEOMETRY: Record<AgentGraphNode["type"], GeometryKind> = {
  agent: "icosahedron",
  skill: "sphere",
  session: "box",
};

const TYPE_BOOST: Record<AgentGraphNode["type"], number> = {
  agent: 4,
  skill: 1,
  session: 0,
};

interface AgentModeOptions {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
}

export function useAgentMode(opts: AgentModeOptions) {
  const [graph, setGraph] = useState<AgentGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchGraph = useCallback(() => {
    setError(null);
    getAgentGraph()
      .then(setGraph)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load graph"));
  }, []);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  const data: UnifiedGraphData = useMemo(() => {
    if (!graph) return { nodes: [], links: [] };
    // Filter out session/chat nodes — agent view shows the agent and its
    // registered skills only.
    const nodes: UnifiedNode[] = graph.nodes
      .filter((n) => n.type !== "session")
      .map((n) => ({
        id: n.id,
        label: n.label,
        kind: n.type,
        degree: 1,
        color: TYPE_COLOR[n.type],
        geometry: TYPE_GEOMETRY[n.type],
        radiusBoost: TYPE_BOOST[n.type],
        meta: { agentType: n.type },
      }));
    const ids = new Set(nodes.map((n) => n.id));
    const links = graph.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, kind: "uses" }));
    return { nodes, links };
  }, [graph]);

  const onNodeClick = useCallback((node: UnifiedNode) => {
    if (node.kind === "skill") opts.onOpenSkill(node.id.replace(/^skill:/, ""));
    else if (node.kind === "session") opts.onSelectSession(node.id.replace(/^session:/, ""));
  }, [opts]);

  return {
    data,
    sidebar: error ? <div className="agent-graph-error">{error}</div> : null,
    filtersBar: null,
    onNodeClick,
    contextMenu: undefined,
    refresh: fetchGraph,
    empty: null,
    nodeCount: graph?.nodes.length ?? 0,
    edgeCount: graph?.edges.length ?? 0,
  };
}
