/**
 * AdvancedPane — the advanced subsite shell. One surface, three lazy tabs
 * (Knowledge / Heartbeat / Dream); the active tab is driven by the
 * `#/advanced/<area>` route (App owns navigation). Tabs stay mounted once
 * visited — graph re-renders are expensive and heartbeat/dream keep
 * stream state.
 */

import { lazy, Suspense } from "react";
import type { AdvancedArea } from "../../routes";
import "./AdvancedPane.css";

const UnifiedGraphView = lazy(() => import("../UnifiedGraphView"));
const HeartbeatView = lazy(() => import("../HeartbeatView"));
const DreamView = lazy(() => import("../DreamView"));

export interface AdvancedPaneProps {
  area: AdvancedArea;
  onAreaChange: (area: AdvancedArea) => void;
  onOpenSkill: (name: string) => void;
  graphSourceFilter: { mode: "file" | "folder"; path: string } | null;
  onGraphSourceFilterHandled: () => void;
  pendingFolderGraph: string | null;
  onPendingFolderGraphHandled: () => void;
  onViewEntityGraph: (path: string) => void;
  onStartGraphIndex: (path: string) => Promise<void>;
  onSpawnSession: (entityId: number, entityName: string) => void;
  onOpenInChat: (sessionId: string) => void;
  onOpenInVault: (path: string) => void;
}

const TABS: ReadonlyArray<{ id: AdvancedArea; label: string; hint: string }> = [
  { id: "graph", label: "Knowledge", hint: "Vault graph + GraphRAG entity search" },
  { id: "heartbeat", label: "Heartbeat", hint: "Scheduled drivers and their runs" },
  { id: "dream", label: "Dream", hint: "Idle-cycle insights and skill suggestions" },
];

export default function AdvancedPane(props: AdvancedPaneProps) {
  const { area, onAreaChange } = props;
  return (
    <div className="advanced-pane">
      <header className="advanced-pane-header">
        <h2>Advanced tools</h2>
        <nav className="advanced-pane-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={area === t.id}
              className={`advanced-pane-tab${area === t.id ? " advanced-pane-tab--active" : ""}`}
              title={t.hint}
              onClick={() => onAreaChange(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <div className="advanced-pane-body">
        <TabPane active={area === "graph"}>
          <UnifiedGraphView
            onOpenSkill={props.onOpenSkill}
            graphSourceFilter={props.graphSourceFilter}
            onGraphSourceFilterHandled={props.onGraphSourceFilterHandled}
            pendingFolderGraph={props.pendingFolderGraph}
            onPendingFolderGraphHandled={props.onPendingFolderGraphHandled}
            onViewEntityGraph={props.onViewEntityGraph}
            onStartGraphIndex={props.onStartGraphIndex}
            onSpawnSession={props.onSpawnSession}
          />
        </TabPane>
        <TabPane active={area === "heartbeat"}>
          <HeartbeatView
            onOpenInChat={props.onOpenInChat}
            onOpenInVault={props.onOpenInVault}
          />
        </TabPane>
        <TabPane active={area === "dream"}>
          <DreamView />
        </TabPane>
      </div>
    </div>
  );
}

/** One mounted-once tab body; hidden via CSS so state survives switches. */
function TabPane({ active, children }: { active: boolean; children: React.ReactNode }) {
  return (
    <div className="advanced-tab" style={{ display: active ? "flex" : "none" }}>
      <Suspense fallback={<AdvancedFallback />}>{children}</Suspense>
    </div>
  );
}

function AdvancedFallback() {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span className="gs-spinner" style={{ width: 22, height: 22, borderWidth: 3 }} />
    </div>
  );
}
