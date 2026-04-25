// Canvas mouse event handlers extracted from SubgraphCanvas.tsx.

import type { SubgraphSimRefs } from "./useSubgraphSim";
import { drawCanvas } from "./useSubgraphSim";
import { distToSegment, nodeRadius } from "./utils";

export function makeCanvasHandlers(refs: SubgraphSimRefs, startRAF: () => void, onSelectEntity: (id: number) => void) {
  function canvasPoint(e: React.MouseEvent, canvas: HTMLCanvasElement) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left - refs.offsetRef.current.x) / refs.scaleRef.current,
      y: (e.clientY - rect.top - refs.offsetRef.current.y) / refs.scaleRef.current,
    };
  }

  function redraw() {
    const sg = refs.subgraphRef.current;
    const canvas = refs.canvasRef.current;
    if (sg && canvas) drawCanvas(canvas, sg, refs.simNodesRef.current, refs);
  }

  function hitTestNode(cx: number, cy: number): number | null {
    const nodes = refs.simNodesRef.current;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = nodeRadius(n.degree) + 4;
      if ((cx - n.x) ** 2 + (cy - n.y) ** 2 <= r * r) return i;
    }
    return null;
  }

  function hitTestEdge(cx: number, cy: number): number | null {
    const nodes = refs.simNodesRef.current;
    const merged = refs.mergedEdgesRef.current;
    const idx = new Map<number, number>();
    nodes.forEach((n, i) => idx.set(n.id, i));
    let best = -1;
    let bestDist = 8;
    for (let gi = 0; gi < merged.length; gi++) {
      const g = merged[gi];
      const ai = idx.get(g.nodeA);
      const bi = idx.get(g.nodeB);
      if (ai === undefined || bi === undefined) continue;
      const d = distToSegment(cx, cy, nodes[ai].x, nodes[ai].y, nodes[bi].x, nodes[bi].y);
      if (d < bestDist) { bestDist = d; best = gi; }
    }
    return best >= 0 ? best : null;
  }

  function onCanvasDown(e: React.MouseEvent) {
    const canvas = refs.canvasRef.current!;
    const { x, y } = canvasPoint(e, canvas);
    const hit = hitTestNode(x, y);
    if (hit !== null) {
      refs.dragRef.current = { idx: hit, moved: false };
    } else {
      refs.panRef.current = { ox: refs.offsetRef.current.x, oy: refs.offsetRef.current.y, mx: e.clientX, my: e.clientY, moved: false };
    }
  }

  function updateEdgeTooltip(e: React.MouseEvent, edgeHit: number | null) {
    const tooltip = refs.edgeTooltipRef.current;
    if (!tooltip) return;
    const selNode = refs.selectedNodeRef.current;
    const highlighted = refs.highlightNodesRef.current;
    const hasFocus = selNode !== null || highlighted.size > 0;
    let showTooltip = false;
    if (hasFocus && edgeHit !== null && refs.simNodesRef.current.length > 0) {
      const g = refs.mergedEdgesRef.current[edgeHit];
      if (g && g.relations.length > 0) {
        const nodes = refs.simNodesRef.current;
        const idxMap = new Map<number, number>();
        nodes.forEach((n, i) => idxMap.set(n.id, i));
        const activeNodes = new Set<number>();
        if (selNode !== null) activeNodes.add(selNode);
        for (const hi of highlighted) activeNodes.add(hi);
        for (const mg of refs.mergedEdgesRef.current) {
          const ai = idxMap.get(mg.nodeA); const bi = idxMap.get(mg.nodeB);
          if (ai === undefined || bi === undefined) continue;
          if (activeNodes.has(ai)) activeNodes.add(bi);
          if (activeNodes.has(bi)) activeNodes.add(ai);
        }
        const ai = idxMap.get(g.nodeA); const bi = idxMap.get(g.nodeB);
        if (ai !== undefined && bi !== undefined && activeNodes.has(ai) && activeNodes.has(bi)) {
          const nameA = nodes[ai]?.name ?? ""; const nameB = nodes[bi]?.name ?? "";
          const rect = (e.target as HTMLElement).getBoundingClientRect();
          const localX = e.clientX - rect.left; const localY = e.clientY - rect.top;
          tooltip.style.display = "block";
          const ttWidth = 220;
          const ttLeft = localX + 14 + ttWidth > rect.width ? localX - ttWidth - 8 : localX + 14;
          tooltip.style.left = `${ttLeft}px`;
          tooltip.style.top = `${Math.max(4, localY - 8)}px`;
          tooltip.innerHTML = g.relations.map((r) => {
            const fromName = r.from === g.nodeA ? nameA : nameB;
            const toName = r.to === g.nodeB ? nameB : nameA;
            return `<div class="kv-edge-tooltip-row"><span class="kv-edge-tooltip-names">${fromName} → ${toName}</span><span class="kv-edge-tooltip-label">${r.label.replace(/_/g, " ")}</span></div>`;
          }).join("");
          showTooltip = true;
        }
      }
    }
    if (!showTooltip) tooltip.style.display = "none";
  }

  function onCanvasMove(e: React.MouseEvent) {
    const canvas = refs.canvasRef.current!;
    const { x: cx, y: cy } = canvasPoint(e, canvas);
    refs.hoverRef.current = hitTestNode(cx, cy);
    const edgeHit = !refs.dragRef.current && !refs.panRef.current ? hitTestEdge(cx, cy) : null;
    const prevEdgeGrp = refs.hoveredEdgeGroupRef.current;
    refs.hoveredEdgeGroupRef.current = edgeHit;
    updateEdgeTooltip(e, edgeHit);
    if (refs.dragRef.current) {
      refs.dragRef.current.moved = true;
      const n = refs.simNodesRef.current[refs.dragRef.current.idx];
      n.x = cx; n.y = cy; n.vx = 0; n.vy = 0; n.pinned = true;
      if (!refs.runningRef.current) { refs.runningRef.current = true; refs.settledRef.current = false; startRAF(); }
    } else if (refs.panRef.current) {
      const dx = e.clientX - refs.panRef.current.mx;
      const dy = e.clientY - refs.panRef.current.my;
      if (Math.abs(dx) + Math.abs(dy) > 2) refs.panRef.current.moved = true;
      refs.offsetRef.current = {
        x: refs.panRef.current.ox + dx,
        y: refs.panRef.current.oy + dy,
      };
      redraw();
    } else {
      if (edgeHit !== prevEdgeGrp) redraw();
    }
  }

  function onCanvasUp(e: React.MouseEvent) {
    const wasNodeClick = refs.dragRef.current && !refs.dragRef.current.moved;
    const wasBackgroundClick = refs.panRef.current && !refs.panRef.current.moved;
    if (wasNodeClick || wasBackgroundClick) {
      const canvas = refs.canvasRef.current!;
      const { x, y } = canvasPoint(e, canvas);
      const nodeHit = hitTestNode(x, y);
      if (nodeHit !== null) {
        if (refs.selectedNodeRef.current === nodeHit) {
          refs.selectedNodeRef.current = null;
        } else {
          refs.selectedNodeRef.current = nodeHit;
          refs.selectedEdgeRef.current = null;
        }
        redraw();
      } else {
        const edgeHit = hitTestEdge(x, y);
        if (edgeHit !== null) {
          if (refs.selectedEdgeRef.current === edgeHit) {
            refs.selectedEdgeRef.current = null;
          } else {
            refs.selectedEdgeRef.current = edgeHit;
            refs.selectedNodeRef.current = null;
          }
          redraw();
        } else {
          if (refs.selectedNodeRef.current !== null || refs.selectedEdgeRef.current !== null) {
            refs.selectedNodeRef.current = null;
            refs.selectedEdgeRef.current = null;
            redraw();
          }
        }
      }
    }
    refs.dragRef.current = null;
    refs.panRef.current = null;
  }

  function onCanvasDblClick(e: React.MouseEvent) {
    const canvas = refs.canvasRef.current!;
    const { x, y } = canvasPoint(e, canvas);
    const nodeHit = hitTestNode(x, y);
    if (nodeHit !== null) {
      const n = refs.simNodesRef.current[nodeHit];
      void onSelectEntity(n.id);
      return;
    }
    const edgeHit = hitTestEdge(x, y);
    if (edgeHit !== null) {
      const merged = refs.mergedEdgesRef.current;
      const g = merged[edgeHit];
      if (g) {
        const node = refs.simNodesRef.current.find((n) => n.id === g.nodeA);
        if (node) void onSelectEntity(node.id);
      }
    }
  }

  function onCanvasWheel(e: React.WheelEvent) {
    e.preventDefault();
    const canvas = refs.canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const sc = refs.scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    refs.offsetRef.current = {
      x: mx - (mx - refs.offsetRef.current.x) * (newSc / sc),
      y: my - (my - refs.offsetRef.current.y) * (newSc / sc),
    };
    refs.scaleRef.current = newSc;
    redraw();
  }

  function onCanvasLeave() {
    if (refs.edgeTooltipRef.current) refs.edgeTooltipRef.current.style.display = "none";
    refs.hoveredEdgeGroupRef.current = null;
    refs.hoverRef.current = null;
    redraw();
  }

  return { onCanvasDown, onCanvasMove, onCanvasUp, onCanvasDblClick, onCanvasWheel, onCanvasLeave, redraw };
}
