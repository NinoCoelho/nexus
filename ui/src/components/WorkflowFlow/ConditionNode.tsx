import { memo, useCallback, useRef } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepRunStatus } from "../../types/workflow";

export interface ConditionNodeData extends Record<string, unknown> {
  stepId: string;
  stepName: string;
  slug: string;
  expression: string;
  selected?: boolean;
  execStatus?: StepRunStatus | null;
  conditionBranch?: "then" | "else" | null;
  canRun?: boolean;
  onRun?: () => void;
  onOpenInspector?: () => void;
  onAddFromHandle?: (nodeId: string, handleId: string, rect: DOMRect) => void;
}

function ConditionNodeComp({ data, id }: NodeProps) {
  const d = data as unknown as ConditionNodeData;
  const pointerStartThen = useRef<{ x: number; y: number } | null>(null);
  const pointerStartElse = useRef<{ x: number; y: number } | null>(null);
  const dragForwardedThen = useRef(false);
  const dragForwardedElse = useRef(false);

  const onRun = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      d.onRun?.();
    },
    [d],
  );

  const onClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      d.onOpenInspector?.();
    },
    [d],
  );

  const makeHandlers = (
    handleId: string,
    startRef: React.MutableRefObject<{ x: number; y: number } | null>,
    forwardedRef: React.MutableRefObject<boolean>,
  ) => ({
    onPointerDown: useCallback((e: React.PointerEvent) => {
      startRef.current = { x: e.clientX, y: e.clientY };
      forwardedRef.current = false;
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    }, []),

    onPointerMove: useCallback((e: React.PointerEvent) => {
      if (!startRef.current || forwardedRef.current) return;
      const dx = e.clientX - startRef.current.x;
      const dy = e.clientY - startRef.current.y;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
        forwardedRef.current = true;
        startRef.current = null;
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
        const handleEl = (e.currentTarget as HTMLElement).querySelector(
          `.wf-handle[data-handleid="${handleId}"]`,
        );
        if (handleEl) {
          handleEl.dispatchEvent(
            new PointerEvent("pointerdown", {
              clientX: e.clientX,
              clientY: e.clientY,
              bubbles: true,
              cancelable: true,
              pointerId: e.pointerId,
              pointerType: e.pointerType,
            }),
          );
        }
      }
    }, []),

    onClick: useCallback(
      (e: React.MouseEvent) => {
        if (forwardedRef.current) {
          forwardedRef.current = false;
          return;
        }
        if (!startRef.current) return;
        const dx = e.clientX - startRef.current.x;
        const dy = e.clientY - startRef.current.y;
        startRef.current = null;
        if (Math.abs(dx) > 4 || Math.abs(dy) > 4) return;
        e.stopPropagation();
        e.preventDefault();
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        d.onAddFromHandle?.(id, handleId, rect);
      },
      [d, id],
    ),
  });

  const thenHandlers = makeHandlers("then", pointerStartThen, dragForwardedThen);
  const elseHandlers = makeHandlers("else", pointerStartElse, dragForwardedElse);

  const statusIcon = d.execStatus === "completed"
    ? "✓"
    : d.execStatus === "failed"
      ? "✗"
      : d.execStatus === "running"
        ? "●"
        : null;

  const branchLabel = d.conditionBranch === "then" ? "T" : d.conditionBranch === "else" ? "F" : null;

  return (
    <div className="wf-cond-wrap" onClick={onClick}>
      <Handle type="target" position={Position.Top} className="wf-handle" id="target" />
      <div className={`wf-cond-diamond${d.selected ? " selected" : ""}`}>
        <div className="wf-cond-name">{d.stepName || "Condition"}</div>
        {d.expression && <div className="wf-cond-expr">{d.expression}</div>}
        {statusIcon && (
          <div className={`wf-node-status wf-status-${d.execStatus}`} style={{ position: "absolute", top: -4, right: -4 }}>
            {statusIcon}
          </div>
        )}
      </div>
      <div className="wf-cond-ports">
        <div className={`wf-cond-port port-then${d.conditionBranch === "then" ? " active-branch" : ""}`}>
          <span className="wf-cond-port-label">
            {branchLabel === "T" ? "✓ T" : "T"}
          </span>
          <div
            className="wf-add-handle"
            data-handle-id="then"
            onPointerDown={thenHandlers.onPointerDown}
            onPointerMove={thenHandlers.onPointerMove}
            onClick={thenHandlers.onClick}
          >
            <Handle
              type="source"
              position={Position.Bottom}
              id="then"
              className="wf-handle wf-handle-then"
            />
            <span className="wf-add-handle-plus">+</span>
          </div>
        </div>
        <div className={`wf-cond-port port-else${d.conditionBranch === "else" ? " active-branch" : ""}`}>
          <span className="wf-cond-port-label">
            {branchLabel === "F" ? "✓ F" : "F"}
          </span>
          <div
            className="wf-add-handle"
            data-handle-id="else"
            onPointerDown={elseHandlers.onPointerDown}
            onPointerMove={elseHandlers.onPointerMove}
            onClick={elseHandlers.onClick}
          >
            <Handle
              type="source"
              position={Position.Bottom}
              id="else"
              className="wf-handle wf-handle-else"
            />
            <span className="wf-add-handle-plus">+</span>
          </div>
        </div>
      </div>
      <button
        className={`wf-node-run-btn${d.execStatus === "completed" ? " wf-run-has-data" : ""}`}
        onClick={onRun}
        disabled={!d.canRun && d.execStatus !== "completed"}
        title={d.execStatus === "completed" ? "Re-run condition" : d.canRun ? "Run condition" : "Prerequisites not met"}
        style={{ position: "relative", marginTop: 4 }}
      >
        ▶
      </button>
    </div>
  );
}

export const ConditionNode = memo(ConditionNodeComp);
