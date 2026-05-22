import { memo, useState, useRef, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepType } from "../../types/workflow";

const ADD_ITEMS: { type: StepType; icon: string; label: string; desc: string }[] = [
  { type: "tool_call", icon: "🔧", label: "Tool Call", desc: "Run a registered tool" },
  { type: "agent_session", icon: "🤖", label: "Agent", desc: "LLM reasoning session" },
  { type: "condition", icon: "🔀", label: "Condition", desc: "Branch on expression" },
  { type: "transform", icon: "🔄", label: "Transform", desc: "Template / data mapping" },
  { type: "delay", icon: "⏱️", label: "Delay", desc: "Wait N seconds" },
  { type: "http_request", icon: "🌐", label: "HTTP", desc: "Call an external API" },
];

export interface AddNodeData extends Record<string, unknown> {
  onAdd?: (type: StepType) => void;
}

function AddNodeComp({ data }: NodeProps) {
  const d = data as unknown as AddNodeData;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <>
      <Handle type="target" position={Position.Top} className="wf-handle" />
      <div
        className="wf-node add-node"
        ref={ref}
        onClick={() => setOpen(!open)}
      >
        <div className="wf-node-body">
          <span className="plus">+</span>
          <span className="hint">Add step</span>
        </div>
        {open && (
          <div
            className="wf-add-menu"
            style={{ position: "absolute", top: "100%", left: 0, marginTop: 4 }}
            onClick={(e) => e.stopPropagation()}
          >
            {ADD_ITEMS.map((item) => (
              <div
                key={item.type}
                className="wf-add-menu-item"
                onClick={() => {
                  d.onAdd?.(item.type);
                  setOpen(false);
                }}
              >
                <span className="icon">{item.icon}</span>
                <span className="label">{item.label}</span>
                <span className="desc">{item.desc}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

export const AddNode = memo(AddNodeComp);
