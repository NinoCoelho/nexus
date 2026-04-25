// Canvas interaction handlers for GraphView.
// Handles mouse events (drag nodes, pan, zoom, click to select) and fit-to-view.

import type React from "react";
import type { GraphData } from "../../api";
import type { SimNode } from "./types";
import { nodeRadius } from "./utils";
import { draw, type DrawState } from "./drawGraph";

interface InteractionDeps {
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  nodesRef: React.MutableRefObject<SimNode[]>;
  runningRef: React.MutableRefObject<boolean>;
  settledRef: React.MutableRefObject<boolean>;
  offsetRef: React.MutableRefObject<{ x: number; y: number }>;
  scaleRef: React.MutableRefObject<number>;
  hoverRef: React.MutableRefObject<number | null>;
  selectedRef: React.MutableRefObject<number | null>;
  dragRef: React.MutableRefObject<{ nodeIdx: number | null; startX: number; startY: number; moved: boolean } | null>;
  panRef: React.MutableRefObject<{ ox: number; oy: number; mx: number; my: number } | null>;
  getFilteredGraph: () => GraphData | null;
  startRAF: () => void;
  getDrawState: () => DrawState;
  onNodeClick: (nodeIdx: number) => void;
}

function canvasPoint(
  e: React.MouseEvent,
  canvas: HTMLCanvasElement,
  offsetRef: React.MutableRefObject<{ x: number; y: number }>,
  scaleRef: React.MutableRefObject<number>,
): { x: number; y: number } {
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const sc = scaleRef.current;
  const { x: ox, y: oy } = offsetRef.current;
  return { x: (mx - ox) / sc, y: (my - oy) / sc };
}

function hitTest(cx: number, cy: number, nodes: SimNode[]): number | null {
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    let r: number;
    if (n.nodeType === "entity") {
      r = 8;
    } else if (n.data) {
      r = nodeRadius(n.data.size) + 4;
    } else continue;
    const dx = cx - n.x; const dy = cy - n.y;
    if (dx * dx + dy * dy <= r * r) return i;
  }
  return null;
}

export function buildCanvasHandlers(deps: InteractionDeps) {
  const {
    canvasRef, nodesRef, runningRef, settledRef,
    offsetRef, scaleRef, hoverRef, selectedRef,
    dragRef, panRef, getFilteredGraph, startRAF,
    getDrawState, onNodeClick,
  } = deps;

  function redraw() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const g = getFilteredGraph();
    const nodes = nodesRef.current;
    if (g && nodes.length > 0) draw(canvas, g, nodes, getDrawState());
  }

  function onMouseDown(e: React.MouseEvent) {
    const canvas = canvasRef.current!;
    const { x, y } = canvasPoint(e, canvas, offsetRef, scaleRef);
    const hit = hitTest(x, y, nodesRef.current);
    if (hit !== null) {
      dragRef.current = { nodeIdx: hit, startX: e.clientX, startY: e.clientY, moved: false };
    } else {
      const { x: ox, y: oy } = offsetRef.current;
      panRef.current = { ox, oy, mx: e.clientX, my: e.clientY };
    }
  }

  function onMouseMove(e: React.MouseEvent) {
    const canvas = canvasRef.current!;
    const { x: cx, y: cy } = canvasPoint(e, canvas, offsetRef, scaleRef);
    const hit = hitTest(cx, cy, nodesRef.current);
    hoverRef.current = hit;

    if (dragRef.current?.nodeIdx !== null && dragRef.current !== null) {
      dragRef.current.moved = true;
      const n = nodesRef.current[dragRef.current.nodeIdx!];
      n.x = cx; n.y = cy;
      n.vx = 0; n.vy = 0;
      n.pinned = true;
      if (!runningRef.current) {
        runningRef.current = true;
        settledRef.current = false;
        startRAF();
      }
    } else if (panRef.current) {
      offsetRef.current = {
        x: panRef.current.ox + e.clientX - panRef.current.mx,
        y: panRef.current.oy + e.clientY - panRef.current.my,
      };
      redraw();
    } else {
      redraw();
    }
  }

  function onMouseUp(e: React.MouseEvent) {
    if (dragRef.current && !dragRef.current.moved) {
      const canvas = canvasRef.current!;
      const { x, y } = canvasPoint(e, canvas, offsetRef, scaleRef);
      const hit = hitTest(x, y, nodesRef.current);
      if (hit !== null) {
        selectedRef.current = hit;
        onNodeClick(hit);
      }
    }
    dragRef.current = null;
    panRef.current = null;
  }

  function onDoubleClick() {
    // Caller handles reset via initSim
  }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    offsetRef.current = {
      x: mx - (mx - ox) * (newSc / sc),
      y: my - (my - oy) * (newSc / sc),
    };
    scaleRef.current = newSc;
    redraw();
  }

  function fitToView() {
    const canvas = canvasRef.current;
    const nodes = nodesRef.current;
    if (!canvas || nodes.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
    }
    const pad = 40;
    const bw = maxX - minX + pad * 2;
    const bh = maxY - minY + pad * 2;
    const sc = Math.min(canvas.width / bw, canvas.height / bh, 2);
    scaleRef.current = sc;
    offsetRef.current = {
      x: (canvas.width - bw * sc) / 2 + (pad - minX) * sc,
      y: (canvas.height - bh * sc) / 2 + (pad - minY) * sc,
    };
    const g = getFilteredGraph();
    if (g) draw(canvas, g, nodes, getDrawState());
  }

  return { onMouseDown, onMouseMove, onMouseUp, onDoubleClick, onWheel, fitToView };
}
