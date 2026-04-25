/**
 * @file Knowledge graph canvas panel for the KnowledgeView.
 *
 * Combines the force simulation (`useSubgraphSim`), mouse/wheel interactions
 * (`makeCanvasHandlers`), and a toolbar with entity search, layout refresh,
 * fit, and zoom controls. All camera logic (pan/zoom/fit) is managed through
 * mutable refs in `SubgraphSimRefs` to avoid unnecessary re-renders during animation.
 */

import { useEffect } from "react";
import type { SubgraphData } from "../../api";
import { drawCanvas, useSubgraphSim } from "./useSubgraphSim";
import type { SubgraphSimRefs } from "./useSubgraphSim";
import { makeCanvasHandlers } from "./useCanvasInteractions";

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

/**
 * Vault entity subgraph visualization panel.
 *
 * Renders a force-directed canvas with an embedded toolbar.
 * When there is no subgraph (`hasSubgraph = false`), displays a contextual
 * empty state — including an index button when `sourceFilter = "file"`.
 *
 * @param subgraphData - Subgraph data to visualize; `null` while no selection is active.
 * @param graphSearch - Active search term (controls highlight of matching nodes).
 * @param graphSearchCount - Number of nodes matching the current search term.
 * @param hasSubgraph - Whether there is enough data to render the graph.
 * @param loading - Shows a "Loading…" placeholder while data is arriving.
 * @param sourceFilter - Current selection context: `"none"`, `"file"`, or `"folder"`.
 * @param sourcePath - Vault path of the selected file or folder; used for the index button.
 * @param onStartGraphIndex - Callback to trigger LLM indexing of a file.
 * @param refs - Shared simulation refs (canvas, nodes, camera, etc.).
 * @param onSelectEntity - Callback when an entity is double-clicked on the canvas.
 * @param onGraphSearchChange - Updates the search term and redraws the canvas.
 * @param onClearGraphSearch - Clears the active search.
 * @param graphSearchValue - Controlled value for the search input.
 */
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
    const minX = xs[lo]; const maxX = xs[hi];
    const minY = ys[lo]; const maxY = ys[hi];

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
  const { onCanvasDown, onCanvasMove, onCanvasUp, onCanvasDblClick, onCanvasWheel, onCanvasLeave, redraw } =
    makeCanvasHandlers(refs, startRAF, onSelectEntity);

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
        onMouseLeave={onCanvasLeave}
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
