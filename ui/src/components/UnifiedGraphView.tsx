/**
 * UnifiedGraphView — tabbed container for the three graph sub-views.
 *
 * - Knowledge: GraphRAG entity/relation graph (extracted from vault files)
 * - Vault: file-link graph (which vault files reference each other)
 * - Agent: skill/session graph (which sessions used which skills)
 *
 * The `graphSourceFilter` prop lets App.tsx navigate the user to a specific
 * file/folder subgraph (e.g. "View entity graph" from a vault file preview).
 */

import { useState } from "react";
import GraphView from "./GraphView";
import AgentGraphView from "./AgentGraphView";
import KnowledgeView from "./KnowledgeView";
import "./UnifiedGraphView.css";

type GraphTab = "knowledge" | "vault" | "agent";

const TABS: { id: GraphTab; label: string }[] = [
  { id: "knowledge", label: "Knowledge" },
  { id: "vault", label: "Vault" },
  { id: "agent", label: "Agent" },
];

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
  graphSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onGraphSourceFilterHandled?: () => void;
  onViewEntityGraph?: (path: string) => void;
  onStartGraphIndex?: (path: string) => void;
  onSpawnSession?: (entityId: number, entityName: string) => void;
}

export default function UnifiedGraphView({ onOpenSkill, onSelectSession, graphSourceFilter, onGraphSourceFilterHandled, onViewEntityGraph, onStartGraphIndex, onSpawnSession }: Props) {
  const [tab, setTab] = useState<GraphTab>("knowledge");

  return (
    <div className="unified-graph-view">
      <div className="unified-graph-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`unified-graph-tab${tab === t.id ? " unified-graph-tab--active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="unified-graph-content">
        {tab === "knowledge" && <KnowledgeView initialSourceFilter={graphSourceFilter} onSourceFilterHandled={onGraphSourceFilterHandled} onViewEntityGraph={onViewEntityGraph} onStartGraphIndex={onStartGraphIndex} onSpawnSession={onSpawnSession} />}
        {tab === "vault" && <GraphView onViewEntityGraph={onViewEntityGraph} />}
        {tab === "agent" && (
          <AgentGraphView onOpenSkill={onOpenSkill} onSelectSession={onSelectSession} />
        )}
      </div>
    </div>
  );
}
