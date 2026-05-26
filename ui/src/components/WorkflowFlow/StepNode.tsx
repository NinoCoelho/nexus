import { memo, useCallback, useRef } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { StepType, StepRunStatus } from "../../types/workflow";

const STEP_ICONS: Record<string, string> = {
  tool_call: "🔧",
  agent_session: "🤖",
  mcp_call: "🔌",
  http_request: "🌐",
  transform: "🔄",
  delay: "⏱️",
  kanban_action: "📋",
  table_action: "📊",
  return_step: "↩",
};

export interface StepNodeData extends Record<string, unknown> {
  stepId: string;
  stepName: string;
  slug: string;
  stepType: StepType;
  summary: string;
  selected?: boolean;
  execStatus?: StepRunStatus | null;
  canRun?: boolean;
  executing?: boolean;
  historyStatus?: StepRunStatus | null;
  historyOutput?: unknown;
  monitorExecuted?: boolean;
  monitorDimmed?: boolean;
  onRun?: () => void;
  onOpenInspector?: () => void;
  onAddFromHandle?: (nodeId: string, handleId: string, rect: DOMRect) => void;
}

function StepNodeComp({ data, id }: NodeProps) {
  const d = data as unknown as StepNodeData;
  const pointerStart = useRef<{ x: number; y: number } | null>(null);
  const dragForwarded = useRef(false);

  const onRun = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (d.canRun || d.execStatus === "completed") {
        d.onOpenInspector?.();
      }
    },
    [d],
  );

  const onDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      d.onOpenInspector?.();
    },
    [d],
  );

  const onSourcePointerDown = useCallback((e: React.PointerEvent) => {
    pointerStart.current = { x: e.clientX, y: e.clientY };
    dragForwarded.current = false;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, []);

  const onSourcePointerMove = useCallback((e: React.PointerEvent) => {
    if (!pointerStart.current || dragForwarded.current) return;
    const dx = e.clientX - pointerStart.current.x;
    const dy = e.clientY - pointerStart.current.y;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
      dragForwarded.current = true;
      pointerStart.current = null;
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      const handleEl = (e.currentTarget as HTMLElement).querySelector(
        '.wf-handle[data-handleid="source"]',
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
  }, []);

  const onSourceClick = useCallback(
    (e: React.MouseEvent) => {
      if (dragForwarded.current) {
        dragForwarded.current = false;
        return;
      }
      if (!pointerStart.current) return;
      const dx = e.clientX - pointerStart.current.x;
      const dy = e.clientY - pointerStart.current.y;
      pointerStart.current = null;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) return;
      e.stopPropagation();
      e.preventDefault();
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      d.onAddFromHandle?.(id, "source", rect);
    },
    [d, id],
  );

  const activeStatus = d.execStatus || d.historyStatus;
  const monitorStatus = d.monitorExecuted
    ? (d.historyStatus === "failed" ? "wf-node-exec-failed" : "wf-node-exec-completed")
    : d.monitorDimmed
      ? "wf-node-exec-dimmed"
      : "";
  const statusClass = d.monitorDimmed !== undefined
    ? monitorStatus
    : d.executing
      ? "wf-node-executing"
      : activeStatus === "failed"
        ? "wf-node-failed"
        : activeStatus === "completed"
          ? "wf-node-completed"
          : d.historyStatus
            ? "wf-node-history-dim"
            : "";

  const statusIcon = activeStatus === "completed"
    ? "\u2713"
    : activeStatus === "failed"
      ? "\u2717"
      : activeStatus === "running"
        ? "\u25CF"
        : activeStatus === "skipped"
          ? "\u2014"
          : null;

  const hasData = d.execStatus === "completed";

  const onStatusClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      d.onOpenInspector?.();
    },
    [d],
  );

  return (
    <div className={`wf-node${d.selected ? " selected" : ""} ${monitorStatus || statusClass}`} style={{ position: "relative" }} onDoubleClick={onDoubleClick}>
      <Handle type="target" position={Position.Top} className="wf-handle" id="target" />
      <div className="wf-node-header">
        <span className="icon">{STEP_ICONS[d.stepType] || "⚙️"}</span>
        <span className="label">Step</span>
        <span className="type-badge">{d.stepType.replace(/_/g, " ")}</span>
      </div>
      <div className="wf-node-body">
        <span className="name">{d.stepName}</span>
        {d.slug && <span className="slug">{d.slug}</span>}
        {d.summary && <span className="summary">{d.summary}</span>}
      </div>

      {activeStatus && statusIcon && (
        <div
          className={`wf-node-status wf-status-${activeStatus}${hasData ? " wf-status-clickable" : ""}`}
          onClick={hasData ? onStatusClick : undefined}
        >
          {statusIcon}
        </div>
      )}

      <button
        className={`wf-node-run-btn${hasData ? " wf-run-has-data" : ""}`}
        onClick={onRun}
        disabled={!d.canRun && !hasData}
        title={hasData ? "Re-run step" : d.canRun ? "Run step" : "Prerequisites not met"}
      >
        ▶
      </button>

      <div
        className="wf-add-handle"
        data-handle-id="source"
        onPointerDown={onSourcePointerDown}
        onPointerMove={onSourcePointerMove}
        onClick={onSourceClick}
      >
        <Handle type="source" position={Position.Bottom} className="wf-handle" id="source" />
        <span className="wf-add-handle-plus">+</span>
      </div>
    </div>
  );
}

export const StepNode = memo(StepNodeComp);
