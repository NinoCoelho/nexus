import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

export interface ConditionNodeData extends Record<string, unknown> {
  stepId: string;
  stepName: string;
  slug: string;
  expression: string;
  selected?: boolean;
  onAddBranch?: (stepId: string, branch: "then" | "else") => void;
}

function ConditionNodeComp({ data }: NodeProps) {
  const d = data as unknown as ConditionNodeData;
  return (
    <div className="wf-cond-wrap">
      <Handle type="target" position={Position.Top} className="wf-handle" id="target" />
      <div className={`wf-cond-diamond${d.selected ? " selected" : ""}`}>
        <div className="wf-cond-name">{d.stepName || "Condition"}</div>
        {d.expression && <div className="wf-cond-expr">{d.expression}</div>}
      </div>
      <div className="wf-cond-ports">
        <div className="wf-cond-port port-then">
          <span className="wf-cond-port-label">T</span>
          <button
            className="wf-cond-port-add"
            onClick={(e) => { e.stopPropagation(); d.onAddBranch?.(d.stepId, "then"); }}
            title="Add true branch"
          >+</button>
          <Handle
            type="source"
            position={Position.Bottom}
            id="then"
            className="wf-handle wf-handle-then"
          />
        </div>
        <div className="wf-cond-port port-else">
          <span className="wf-cond-port-label">F</span>
          <button
            className="wf-cond-port-add"
            onClick={(e) => { e.stopPropagation(); d.onAddBranch?.(d.stepId, "else"); }}
            title="Add false branch"
          >+</button>
          <Handle
            type="source"
            position={Position.Bottom}
            id="else"
            className="wf-handle wf-handle-else"
          />
        </div>
      </div>
    </div>
  );
}

export const ConditionNode = memo(ConditionNodeComp);
