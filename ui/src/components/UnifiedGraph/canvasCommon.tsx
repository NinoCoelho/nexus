/**
 * Shared pieces used by both the 2D and 3D graph canvases: the link-relation
 * tooltip, the right-click context menu, and the auto-fit-on-data-swap logic.
 */

import { useCallback, useEffect, useRef } from "react";
import { escapeHtml } from "./threeHelpers";
import type { ContextMenuItem, UnifiedGraphData, UnifiedLink, UnifiedNode } from "./types";

// ── Link tooltip ────────────────────────────────────────────────────────────

export interface LinkTooltipApi {
  tooltipRef: React.RefObject<HTMLDivElement | null>;
  onLinkHover: (link: object | null) => void;
  onNodeHover: () => void;
}

/**
 * Document-level mousemove tooltip showing the relations carried by a
 * hovered link (deduped, capped). Lives outside React's tree so the canvas
 * keeps its native pointer events for OrbitControls / pan-drag.
 */
export function useLinkTooltip(): LinkTooltipApi {
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const mousePosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoveredLinkRef = useRef<UnifiedLink | null>(null);

  const render = useCallback(() => {
    const tt = tooltipRef.current;
    if (!tt) return;
    const link = hoveredLinkRef.current;
    if (!link || !link.relations || link.relations.length === 0) {
      tt.style.display = "none";
      return;
    }
    const wrap = tt.parentElement;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const localX = mousePosRef.current.x - rect.left;
    const localY = mousePosRef.current.y - rect.top;
    const ttWidth = 240;
    const ttLeft = localX + 14 + ttWidth > rect.width ? localX - ttWidth - 8 : localX + 14;
    tt.style.left = `${ttLeft}px`;
    tt.style.top = `${Math.max(4, localY - 8)}px`;
    tt.style.display = "block";
    // Dedupe by (from|to|label) to avoid the tooltip ballooning when two
    // entities have many parallel edges with the same relation.
    const seen = new Set<string>();
    const unique: typeof link.relations = [];
    for (const r of link.relations) {
      const key = `${r.from}|${r.to}|${r.label || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(r);
    }
    const cap = 8;
    const shown = unique.slice(0, cap);
    const rest = unique.length - shown.length;
    tt.innerHTML = shown
      .map((r) => {
        const label = (r.label || "").replace(/_/g, " ");
        const labelHtml = label
          ? `<span class="kv-edge-tooltip-label">${escapeHtml(label)}</span>`
          : "";
        return `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-names">${escapeHtml(r.from)} → ${escapeHtml(r.to)}</span>${labelHtml}</div>`;
      })
      .join("") + (rest > 0 ? `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-label">+${rest} more</span></div>` : "");
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      mousePosRef.current = { x: e.clientX, y: e.clientY };
      if (hoveredLinkRef.current) render();
    };
    window.addEventListener("mousemove", handler, { passive: true });
    return () => window.removeEventListener("mousemove", handler);
  }, [render]);

  const onLinkHover = useCallback((link: object | null) => {
    hoveredLinkRef.current = (link as UnifiedLink | null) ?? null;
    render();
  }, [render]);

  const onNodeHover = useCallback(() => {
    hoveredLinkRef.current = null;
    if (tooltipRef.current) tooltipRef.current.style.display = "none";
  }, []);

  return { tooltipRef, onLinkHover, onNodeHover };
}

// ── Context menu ────────────────────────────────────────────────────────────

export function GraphContextMenu({
  menu,
  onClose,
}: {
  menu: { node: UnifiedNode; items: ContextMenuItem[]; x: number; y: number };
  onClose: () => void;
}) {
  useEffect(() => {
    const handler = () => onClose();
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [onClose]);

  return (
    <div
      className="kv-context-menu"
      style={{ left: menu.x, top: menu.y }}
      onClick={(e) => e.stopPropagation()}
    >
      {menu.items.map((item, i) => (
        <button
          key={i}
          className="kv-context-menu-item"
          onClick={() => { item.onClick(); onClose(); }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

// ── Auto-fit on substantial data change ─────────────────────────────────────

/**
 * Tracks the node-id signature of the dataset. On a *substantial* change
 * (initial load, mode switch, subgraph swap) returns a new "pending fit"
 * token the canvas should consume on the next engine stop. Pure user drags
 * never change node ids, so the camera is never snapped back mid-interaction.
 */
export function usePendingFit(data: UnifiedGraphData): React.RefObject<boolean> {
  const pendingFitRef = useRef(false);
  const lastSigRef = useRef<string>("");
  useEffect(() => {
    if (!data.nodes.length) return;
    const sig = data.nodes.map((n) => n.id).sort().join("|");
    if (sig === lastSigRef.current) return;
    lastSigRef.current = sig;
    pendingFitRef.current = true;
  }, [data]);
  return pendingFitRef;
}
