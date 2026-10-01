/**
 * AdvancedPane — renders one of the advanced-only views (knowledge graph,
 * heartbeat, dream) based on the `#/advanced/<area>` route. Not part of the
 * main nav; reachable from Settings → Advanced tools and deep links.
 */

import { Suspense, lazy } from "react";

const UnifiedGraphView = lazy(() => import("../UnifiedGraphView"));
const HeartbeatView = lazy(() => import("../HeartbeatView"));
const DreamView = lazy(() => import("../DreamView"));

export interface AdvancedPaneProps {
  area: "graph" | "heartbeat" | "dream";
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

export default function AdvancedPane(props: AdvancedPaneProps) {
  const { area, onOpenInChat, onOpenInVault } = props;
  return (
    <Suspense fallback={<AdvancedFallback />}>
      {area === "graph" && (
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
      )}
      {area === "heartbeat" && (
        <HeartbeatView onOpenInChat={onOpenInChat} onOpenInVault={onOpenInVault} />
      )}
      {area === "dream" && <DreamView />}
    </Suspense>
  );
}

function AdvancedFallback() {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span className="gs-spinner" style={{ width: 22, height: 22, borderWidth: 3 }} />
    </div>
  );
}
