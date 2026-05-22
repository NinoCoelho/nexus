import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { TriggerType } from "../../types/workflow";

const TRIGGER_ICONS: Record<string, string> = {
  webhook: "🔗",
  fs_watch: "📁",
  schedule: "📅",
  manual: "👆",
  event: "📡",
};

export interface TriggerNodeData extends Record<string, unknown> {
  triggerType: TriggerType;
  triggerId: string;
  label: string;
  detail: string;
  selected?: boolean;
}

function TriggerNodeComp({ data }: NodeProps) {
  const d = data as unknown as TriggerNodeData;
  return (
    <div className={`wf-node trigger-node${d.selected ? " selected" : ""}`}>
      <div className="wf-node-header">
        <span className="icon">{TRIGGER_ICONS[d.triggerType] || "⚡"}</span>
        <span className="label">Trigger</span>
        <span className="type-badge">{d.triggerType === "fs_watch" ? "File Watch" : d.triggerType}</span>
      </div>
      {d.detail && (
        <div className="wf-node-body">
          <span className="summary">{d.detail}</span>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="wf-handle" />
    </div>
  );
}

export const TriggerNode = memo(TriggerNodeComp);
