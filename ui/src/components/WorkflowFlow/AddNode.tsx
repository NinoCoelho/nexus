import { memo, useState, useRef, useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepType } from "../../types/workflow";
import { STEP_PALETTE, TRIGGER_PALETTE } from "./index";

export interface AddNodeData extends Record<string, unknown> {
  onAdd?: (type: StepType | "trigger") => void;
  includeTriggers?: boolean;
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
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
      >
        <span className="wf-add-pill-icon">+</span>
        {open && (
          <div className="wf-add-menu" onClick={(e) => e.stopPropagation()}>
            {d.includeTriggers ? (
              TRIGGER_PALETTE.map((item) => (
                <div
                  key={item.type}
                  className="wf-add-menu-item"
                  onClick={() => { d.onAdd?.(`trigger-${item.type}` as "trigger"); setOpen(false); }}
                >
                  <span className="wf-add-menu-icon">{item.icon}</span>
                  <div className="wf-add-menu-text">
                    <span className="wf-add-menu-label">{item.tip}</span>
                  </div>
                </div>
              ))
            ) : (
              STEP_PALETTE.map((item) => (
                <div
                  key={item.type}
                  className="wf-add-menu-item"
                  onClick={() => { d.onAdd?.(item.type); setOpen(false); }}
                >
                  <span className="wf-add-menu-icon">{item.icon}</span>
                  <div className="wf-add-menu-text">
                    <span className="wf-add-menu-label">{item.tip}</span>
                    <span className="wf-add-menu-desc">{item.desc}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </>
  );
}

export const AddNode = memo(AddNodeComp);
