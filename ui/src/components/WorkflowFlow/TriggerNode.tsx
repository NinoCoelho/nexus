import { memo, useCallback, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
  hasActiveRun?: boolean;
  onRunTrigger?: () => void;
  onRunAll?: () => void;
  onAddFromHandle?: (nodeId: string, handleId: string, rect: DOMRect) => void;
}

function TriggerNodeComp({ data, id }: NodeProps) {
  const d = data as unknown as TriggerNodeData;
  const [showMenu, setShowMenu] = useState(false);
  const [menuPos, setMenuPos] = useState({ x: 0, y: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const pointerStart = useRef<{ x: number; y: number } | null>(null);
  const dragForwarded = useRef(false);

  const onPlay = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      setMenuPos({ x: rect.right, y: rect.bottom + 4 });
      setShowMenu((prev) => !prev);
    },
    [],
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

  return (
    <div className={`wf-node trigger-node${d.selected ? " selected" : ""}`} style={{ position: "relative" }}>
      <div className="wf-node-header">
        <span className="icon">{TRIGGER_ICONS[d.triggerType] || "⚡"}</span>
        <span className="label">Trigger</span>
        <span className="type-badge">
          {d.triggerType === "fs_watch" ? "File Watch" : d.triggerType}
        </span>
        <button
          ref={btnRef}
          className="wf-play-btn"
          onClick={onPlay}
          title="Run workflow"
        >
          ▶
        </button>
      </div>
      {d.detail && (
        <div className="wf-node-body">
          <span className="summary">{d.detail}</span>
          {d.hasActiveRun && <span className="wf-trigger-active-label">Session active</span>}
        </div>
      )}

      {showMenu && createPortal(
        <div className="wf-trigger-run-backdrop" onClick={() => setShowMenu(false)} />,
        document.body,
      )}
      {showMenu && createPortal(
        <div
          className="wf-trigger-run-dropdown"
          style={{ left: menuPos.x, top: menuPos.y }}
        >
          <button
            className="wf-trigger-run-option"
            onClick={(e) => {
              e.stopPropagation();
              setShowMenu(false);
              d.onRunTrigger?.();
            }}
          >
            <span className="wf-tro-icon">▶</span>
            <span className="wf-tro-text">
              <span className="wf-tro-label">Run Trigger Only</span>
              <span className="wf-tro-desc">Execute trigger, then step-by-step</span>
            </span>
          </button>
          <button
            className="wf-trigger-run-option"
            onClick={(e) => {
              e.stopPropagation();
              setShowMenu(false);
              d.onRunAll?.();
            }}
          >
            <span className="wf-tro-icon">⏩</span>
            <span className="wf-tro-text">
              <span className="wf-tro-label">Run All Steps</span>
              <span className="wf-tro-desc">Execute everything at once</span>
            </span>
          </button>
        </div>,
        document.body,
      )}

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

export const TriggerNode = memo(TriggerNodeComp);
