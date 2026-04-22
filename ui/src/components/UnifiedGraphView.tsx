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
}

export default function UnifiedGraphView({ onOpenSkill, onSelectSession, graphSourceFilter, onGraphSourceFilterHandled, onViewEntityGraph }: Props) {
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
        {tab === "knowledge" && <KnowledgeView initialSourceFilter={graphSourceFilter} onSourceFilterHandled={onGraphSourceFilterHandled} onViewEntityGraph={onViewEntityGraph} />}
        {tab === "vault" && <GraphView onViewEntityGraph={onViewEntityGraph} />}
        {tab === "agent" && (
          <AgentGraphView onOpenSkill={onOpenSkill} onSelectSession={onSelectSession} />
        )}
      </div>
    </div>
  );
}
