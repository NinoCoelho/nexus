// Sub-component for KnowledgeView: canvas panel with force-directed graph and toolbar.

import { useEffect } from "react";
import type { SubgraphData } from "../../api";
import { distToSegment, nodeRadius } from "./utils";
import { drawCanvas, useSubgraphSim } from "./useSubgraphSim";
import type { SubgraphSimRefs } from "./useSubgraphSim";

interface SubgraphCanvasProps {
  subgraphData: SubgraphData | null;
  graphSearch: string;
  graphSearchCount: number;
  hasSubgraph: boolean;
  loading: boolean;
  sourceFilter: "none" | "file" | "folder";
  sourcePath: string;
  onStartGraphIndex?: (path: string) => void;
  refs: SubgraphSimRefs;
  onSelectEntity: (id: number) => void;
  onGraphSearchChange: (value: string) => void;
  onClearGraphSearch: () => void;
  graphSearchValue: string;
}

export function SubgraphCanvas({
  subgraphData,
  graphSearch,
  graphSearchCount,
  hasSubgraph,
  loading,
  sourceFilter,
  sourcePath,
  onStartGraphIndex,
  refs,
  onSelectEntity,
  onGraphSearchChange,
  onClearGraphSearch,
  graphSearchValue,
}: SubgraphCanvasProps) {
  function fitGraph() {
    const nodes = refs.simNodesRef.current;
    const canvas = refs.canvasRef.current;
    if (!canvas || nodes.length === 0) return;
    const padding = 60;

    // Percentile-based bounds: ignore the worst 5% outliers on each side
    const xs = nodes.map((nd) => nd.x).sort((a, b) => a - b);
    const ys = nodes.map((nd) => nd.y).sort((a, b) => a - b);
    const lo = Math.floor(nodes.length * 0.03);
    const hi = Math.ceil(nodes.length * 0.97) - 1;
    const minX = xs[lo];
    const maxX = xs[hi];
    const minY = ys[lo];
    const maxY = ys[hi];

    const gw = (maxX - minX) || 1;
    const gh = (maxY - minY) || 1;
    const cw = canvas.width - padding * 2;
    const ch = canvas.height - padding * 2;
    // Minimum scale so nodes are always visible; maximum 2.5 for readability
    const sc = Math.max(0.3, Math.min(cw / gw, ch / gh, 2.5));
    refs.scaleRef.current = sc;
    refs.offsetRef.current = {
      x: (canvas.width - gw * sc) / 2 - minX * sc,
      y: (canvas.height - gh * sc) / 2 - minY * sc,
    };
    redraw();
  }

  const { startRAF } = useSubgraphSim(subgraphData, refs, fitGraph);

  // ResizeObserver to keep canvas dimensions in sync with its parent
  useEffect(() => {
    const canvas = refs.canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;
    const ro = new ResizeObserver(() => {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      const sg = refs.subgraphRef.current;
      const nodes = refs.simNodesRef.current;
      if (sg && nodes.length > 0) drawCanvas(canvas, sg, nodes, refs);
    });
    ro.observe(parent);
    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;
    return () => ro.disconnect();
  }, []);

  function redraw() {
    const sg = refs.subgraphRef.current;
    const canvas = refs.canvasRef.current;
    if (sg && canvas) drawCanvas(canvas, sg, refs.simNodesRef.current, refs);
  }

  function canvasPoint(e: React.MouseEvent) {
    const canvas = refs.canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left - refs.offsetRef.current.x) / refs.scaleRef.current,
      y: (e.clientY - rect.top - refs.offsetRef.current.y) / refs.scaleRef.current,
    };
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
    const { x, y } = canvasPoint(e);
    const hit = hitTestNode(x, y);
    if (hit !== null) {
      refs.dragRef.current = { idx: hit, moved: false };
    } else {
      refs.panRef.current = { ox: refs.offsetRef.current.x, oy: refs.offsetRef.current.y, mx: e.clientX, my: e.clientY };
    }
  }

  function onCanvasMove(e: React.MouseEvent) {
    const { x: cx, y: cy } = canvasPoint(e);
    refs.hoverRef.current = hitTestNode(cx, cy);

    const selNode = refs.selectedNodeRef.current;
    const highlighted = refs.highlightNodesRef.current;
    const hasFocus = selNode !== null || highlighted.size > 0;

    // Build activeNodes set for tooltip gating
    const activeNodesForTooltip = new Set<number>();
    if (hasFocus) {
      if (selNode !== null) activeNodesForTooltip.add(selNode);
      for (const hi of highlighted) activeNodesForTooltip.add(hi);
      const mergedEdges = refs.mergedEdgesRef.current;
      const nodes = refs.simNodesRef.current;
      const idxMap = new Map<number, number>();
      nodes.forEach((n, i) => idxMap.set(n.id, i));
      for (const g of mergedEdges) {
        const ai = idxMap.get(g.nodeA);
        const bi = idxMap.get(g.nodeB);
        if (ai === undefined || bi === undefined) continue;
        if (activeNodesForTooltip.has(ai)) activeNodesForTooltip.add(bi);
        if (activeNodesForTooltip.has(bi)) activeNodesForTooltip.add(ai);
      }
    }

    // Edge hover tooltip — only on highlighted edges
    const edgeHit = !refs.dragRef.current && !refs.panRef.current ? hitTestEdge(cx, cy) : null;
    const prevEdgeGrp = refs.hoveredEdgeGroupRef.current;
    refs.hoveredEdgeGroupRef.current = edgeHit;
    const tooltip = refs.edgeTooltipRef.current;

    if (tooltip) {
      let showTooltip = false;
      if (hasFocus && edgeHit !== null && refs.simNodesRef.current.length > 0) {
        const g = refs.mergedEdgesRef.current[edgeHit];
        if (g && g.relations.length > 0) {
          const nodes = refs.simNodesRef.current;
          const idxMap = new Map<number, number>();
          nodes.forEach((n, i) => idxMap.set(n.id, i));
          const ai = idxMap.get(g.nodeA);
          const bi = idxMap.get(g.nodeB);
          // Only show on edges where both endpoints are active (visually highlighted)
          if (ai !== undefined && bi !== undefined && activeNodesForTooltip.has(ai) && activeNodesForTooltip.has(bi)) {
            const nameA = nodes[ai]?.name ?? "";
            const nameB = nodes[bi]?.name ?? "";
            const rect = (e.target as HTMLElement).getBoundingClientRect();
            const localX = e.clientX - rect.left;
            const localY = e.clientY - rect.top;
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

    if (refs.dragRef.current) {
      refs.dragRef.current.moved = true;
      const n = refs.simNodesRef.current[refs.dragRef.current.idx];
      n.x = cx; n.y = cy; n.vx = 0; n.vy = 0; n.pinned = true;
      if (!refs.runningRef.current) { refs.runningRef.current = true; refs.settledRef.current = false; startRAF(); }
    } else if (refs.panRef.current) {
      refs.offsetRef.current = {
        x: refs.panRef.current.ox + e.clientX - refs.panRef.current.mx,
        y: refs.panRef.current.oy + e.clientY - refs.panRef.current.my,
      };
      redraw();
    } else {
      if (edgeHit !== prevEdgeGrp) redraw();
    }
  }

  function onCanvasUp(e: React.MouseEvent) {
    if (refs.dragRef.current && !refs.dragRef.current.moved) {
      const { x, y } = canvasPoint(e);
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
    const { x, y } = canvasPoint(e);
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

  function refreshGraph() {
    const nodes = refs.simNodesRef.current;
    const canvas = refs.canvasRef.current;
    if (!canvas || nodes.length === 0) return;
    const w = canvas.width || 800;
    const h = canvas.height || 600;
    for (const n of nodes) {
      n.x = w * 0.2 + Math.random() * w * 0.6;
      n.y = h * 0.2 + Math.random() * h * 0.6;
      n.vx = 0; n.vy = 0; n.pinned = false;
    }
    refs.tickRef.current = 0;
    refs.settledRef.current = false;
    refs.runningRef.current = true;
    refs.scaleRef.current = 1;
    refs.offsetRef.current = { x: 0, y: 0 };
    startRAF();
  }

  function zoomGraph(factor: number) {
    const canvas = refs.canvasRef.current;
    if (!canvas) return;
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const sc = refs.scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    refs.offsetRef.current = {
      x: cx - (cx - refs.offsetRef.current.x) * (newSc / sc),
      y: cy - (cy - refs.offsetRef.current.y) * (newSc / sc),
    };
    refs.scaleRef.current = newSc;
    redraw();
  }

  return (
    <div className="kv-graph">
      <canvas
        ref={refs.canvasRef}
        className="kv-canvas"
        onMouseDown={onCanvasDown}
        onMouseMove={onCanvasMove}
        onMouseUp={onCanvasUp}
        onDoubleClick={onCanvasDblClick}
        onWheel={onCanvasWheel}
        onMouseLeave={() => {
          if (refs.edgeTooltipRef.current) refs.edgeTooltipRef.current.style.display = "none";
          refs.hoveredEdgeGroupRef.current = null;
          refs.hoverRef.current = null;
          redraw();
        }}
      />
      <div ref={refs.edgeTooltipRef} className="kv-edge-tooltip" />
      <div className="kv-graph-toolbar">
        <div className="kv-graph-search">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="7" cy="7" r="5" />
            <line x1="11" y1="11" x2="14" y2="14" />
          </svg>
          <input
            className="kv-graph-search-input"
            type="text"
            placeholder="Find entity…"
            value={graphSearchValue}
            onChange={(e) => onGraphSearchChange(e.target.value)}
            disabled={!hasSubgraph}
          />
          {graphSearch && <span className="kv-graph-search-count">{graphSearchCount}</span>}
          {graphSearch && <button className="kv-graph-search-clear" onClick={onClearGraphSearch}>&times;</button>}
        </div>
        <div className="kv-graph-tools">
          <button className="kv-tool-btn" onClick={refreshGraph} title="Restart layout" disabled={!hasSubgraph}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1 1v5h5" />
              <path d="M3.5 11A6 6 0 1 0 4.5 4.5L1 6" />
            </svg>
          </button>
          <button className="kv-tool-btn" onClick={fitGraph} title="Fit to view" disabled={!hasSubgraph}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="1" y="1" width="6" height="6" rx="1" />
              <rect x="9" y="1" width="6" height="6" rx="1" />
              <rect x="1" y="9" width="6" height="6" rx="1" />
              <rect x="9" y="9" width="6" height="6" rx="1" />
            </svg>
          </button>
          <button className="kv-tool-btn" onClick={() => zoomGraph(1.3)} title="Zoom in" disabled={!hasSubgraph}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="7" cy="7" r="5" />
              <line x1="7" y1="5" x2="7" y2="9" />
              <line x1="5" y1="7" x2="9" y2="7" />
              <line x1="11" y1="11" x2="14" y2="14" />
            </svg>
          </button>
          <button className="kv-tool-btn" onClick={() => zoomGraph(0.7)} title="Zoom out" disabled={!hasSubgraph}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="7" cy="7" r="5" />
              <line x1="5" y1="7" x2="9" y2="7" />
              <line x1="11" y1="11" x2="14" y2="14" />
            </svg>
          </button>
        </div>
      </div>
      {!hasSubgraph && (
        <div className="kv-graph-empty">
          {sourceFilter === "file" && sourcePath && !loading ? (
            <>
              <p>No entities found for this file.</p>
              {onStartGraphIndex && (
                <button className="kv-index-file-btn" onClick={() => onStartGraphIndex(sourcePath)}>
                  <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
                    <polyline points="7,8 10,4 13,8" />
                    <line x1="10" y1="4" x2="10" y2="14" />
                  </svg>
                  Index this file
                </button>
              )}
              <span className="kv-graph-empty-hint">Extracts entities and relationships using the LLM</span>
            </>
          ) : loading ? (
            <p>Loading…</p>
          ) : (
            <p>Search or click an entity to explore its knowledge graph</p>
          )}
        </div>
      )}
    </div>
  );
}
