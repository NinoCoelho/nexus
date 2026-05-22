import { memo, useState, useRef, useEffect } from "react";
import { Position } from "@xyflow/react";
import { Handle } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepType } from "../../types/workflow";

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
        className="wf-add-pill"
        ref={ref}
        onClick={() => setOpen(!open)}
      >
        <span className="wf-add-pill-icon">+</span>
        {open && (
          <div className="wf-add-menu" onClick={(e) => e.stopPropagation()}>
            {MENU_ITEMS.map((item) => (
              <div
                key={item.type}
                className="wf-add-menu-item"
                onClick={() => {
                  d.onAdd?.(item.type);
                  setOpen(false);
                }}
              >
                <span className="wf-add-menu-icon">{item.icon}</span>
                <div className="wf-add-menu-text">
                  <span className="wf-add-menu-label">{item.label}</span>
                  <span className="wf-add-menu-desc">{item.desc}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

const MENU_ITEMS: { type: StepType; icon: string; label: string; desc: string }[] = [
  { type: "tool_call", icon: "🔧", label: "Tool Call", desc: "Run a tool" },
  { type: "agent_session", icon: "🤖", label: "Agent", desc: "LLM session" },
  { type: "condition", icon: "◇", label: "Condition", desc: "Branch" },
  { type: "transform", icon: "🔄", label: "Transform", desc: "Map data" },
  { type: "delay", icon: "⏱", label: "Delay", desc: "Wait" },
  { type: "http_request", icon: "🌐", label: "HTTP", desc: "Call API" },
];

export const AddNode = memo(AddNodeComp);
