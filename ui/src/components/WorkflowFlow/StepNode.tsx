import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepType } from "../../types/workflow";

const STEP_ICONS: Record<string, string> = {
  tool_call: "🔧",
  agent_session: "🤖",
  mcp_call: "🔌",
  http_request: "🌐",
  condition: "🔀",
  transform: "🔄",
  delay: "⏱️",
};

export interface StepNodeData extends Record<string, unknown> {
  stepId: string;
  stepName: string;
  stepType: StepType;
  summary: string;
  condition?: string;
  selected?: boolean;
}

function StepNodeComp({ data }: NodeProps) {
  const d = data as unknown as StepNodeData;
  return (
    <div className={`wf-node${d.selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Top} className="wf-handle" />
      <div className="wf-node-header">
        <span className="icon">{STEP_ICONS[d.stepType] || "⚙️"}</span>
        <span className="label">Step</span>
        <span className="type-badge">{d.stepType.replace(/_/g, " ")}</span>
      </div>
      <div className="wf-node-body">
        <span className="name">{d.stepName}</span>
        {d.summary && <span className="summary">{d.summary}</span>}
        {d.condition && (
          <span className="condition-tag">if: {d.condition}</span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="wf-handle" />
    </div>
  );
}

export const StepNode = memo(StepNodeComp);
