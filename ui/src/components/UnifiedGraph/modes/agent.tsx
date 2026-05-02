/**
 * Agent mode — agent/skill/session graph for the Nexus runtime.
 *
 * Hosts the SkillWizard launch points: a "+" affordance in the filter bar
 * (always visible while the agent tab is active) and a "Add a new skill…"
 * entry on the right-click menu of the agent node. Both routes open the
 * same SkillWizard modal — the entry-point on the graph is the friendly
 * one for non-technical users.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAgentGraph, type AgentGraphData, type AgentGraphNode } from "../../../api";
import SkillWizard from "../../SkillWizard/SkillWizard";
import type { ContextMenuItem, GeometryKind, UnifiedGraphData, UnifiedNode } from "../types";

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
  const { t } = useTranslation("skillWizard");
  const [graph, setGraph] = useState<AgentGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

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

  const openWizard = useCallback(() => setWizardOpen(true), []);
  const closeWizard = useCallback(() => {
    setWizardOpen(false);
    // Refresh in case a skill was authored — Phase 4 wires the actual create
    // path; for now this keeps the graph in sync once it does.
    fetchGraph();
  }, [fetchGraph]);

  const contextMenu = useCallback(
    (node: UnifiedNode): ContextMenuItem[] => {
      // Right-click on the agent (the central NEXUS node) offers the wizard.
      // Skills can keep their existing click-to-open behavior; cluttering the
      // skill context menu with "add a new skill" would be misleading.
      if (node.kind === "agent") {
        return [{ label: t("menu.addSkill"), onClick: openWizard }];
      }
      return [];
    },
    [t, openWizard],
  );

  // Filter bar lives in the toolbar region above the canvas — always visible
  // while the agent tab is active so non-technical users don't have to
  // discover the right-click menu.
  const filtersBar = (
    <>
      <button
        type="button"
        className="agent-graph-add-skill-btn"
        onClick={openWizard}
        title={t("openButton")}
      >
        <span aria-hidden="true">+</span>
        <span>{t("openButton")}</span>
      </button>
      {wizardOpen && <SkillWizard onClose={closeWizard} />}
    </>
  );

  return {
    data,
    sidebar: error ? <div className="agent-graph-error">{error}</div> : null,
    filtersBar,
    onNodeClick,
    contextMenu,
    refresh: fetchGraph,
    empty: null,
    nodeCount: graph?.nodes.length ?? 0,
    edgeCount: graph?.edges.length ?? 0,
  };
}
