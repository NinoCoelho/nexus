/**
 * Bubble — the floating, draggable coordinator orb.
 *
 * Pointer-event drag with a 6px click-vs-drag threshold: a press that moves
 * less than the threshold toggles the popup panel, anything else drags.
 * Position (top-left px) is owned by the parent and persisted; the orb
 * clamps itself into the viewport.
 */

import { useCallback, useRef } from "react";

export interface BubblePos {
  x: number;
  y: number;
}

interface Props {
  /** Dominant mood — drives the orb's animation. */
  status: "idle" | "processing" | "hitl" | "error" | "settled";
  name: string;
  open: boolean;
  onToggle: () => void;
  pos: BubblePos;
  onPosChange: (p: BubblePos) => void;
}

export const ORB_SIZE = 54;
const DRAG_THRESHOLD = 6;
const EDGE_MARGIN = 8;

export function clampBubblePos(p: BubblePos): BubblePos {
  const maxX = Math.max(EDGE_MARGIN, window.innerWidth - ORB_SIZE - EDGE_MARGIN);
  const maxY = Math.max(EDGE_MARGIN, window.innerHeight - ORB_SIZE - EDGE_MARGIN);
  return {
    x: Math.min(Math.max(p.x, EDGE_MARGIN), maxX),
    y: Math.min(Math.max(p.y, EDGE_MARGIN), maxY),
  };
}

export default function Bubble({ status, name, open, onToggle, pos, onPosChange }: Props) {
  const dragState = useRef<{ startX: number; startY: number; origin: BubblePos; dragging: boolean } | null>(null);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      dragState.current = { startX: e.clientX, startY: e.clientY, origin: pos, dragging: false };
    },
    [pos],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const st = dragState.current;
      if (!st) return;
      const dx = e.clientX - st.startX;
      const dy = e.clientY - st.startY;
      if (!st.dragging && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      st.dragging = true;
      onPosChange(clampBubblePos({ x: st.origin.x + dx, y: st.origin.y + dy }));
    },
    [onPosChange],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const st = dragState.current;
      dragState.current = null;
      try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already gone */ }
      if (!st || !st.dragging) onToggle();
    },
    [onToggle],
  );

  return (
    <div
      className={`nxcb-orb nxcb-orb--${status}${open ? " nxcb-orb--open" : ""}`}
      style={{ left: pos.x, top: pos.y, width: ORB_SIZE, height: ORB_SIZE }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      role="button"
      aria-label={`${name} — open master chat`}
      title={name}
    >
      {/* orbit ring (processing) */}
      {status === "processing" && (
        <span className="nxcb-orbit" aria-hidden="true">
          <span className="nxcb-orbit-dot" />
          <span className="nxcb-orbit-dot" />
          <span className="nxcb-orbit-dot" />
        </span>
      )}
      {/* the creature's face */}
      <span className="nxcb-face" aria-hidden="true">
        <span className="nxcb-eye" />
        <span className="nxcb-eye" />
      </span>
      {/* status badges — processing and hitl can coexist */}
      {status === "processing" && (
        <span className="nxcb-badge nxcb-badge--processing" aria-label="processing">
          <span className="nxcb-badge-dot" />
          <span className="nxcb-badge-dot" />
          <span className="nxcb-badge-dot" />
        </span>
      )}
      {status === "hitl" && (
        <span className="nxcb-badge nxcb-badge--hitl" aria-label="waiting for your answer">?</span>
      )}
      {status === "error" && (
        <span className="nxcb-badge nxcb-badge--error" aria-label="last turn errored">!</span>
      )}
      {status === "settled" && (
        <span className="nxcb-badge nxcb-badge--settled" aria-label="done">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 8.5l3.5 3.5L13 5" />
          </svg>
        </span>
      )}
    </div>
  );
}
