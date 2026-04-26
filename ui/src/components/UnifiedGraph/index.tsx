/**
 * UnifiedGraph — the unified 3D graph view.
 *
 * Mounts a single ForceGraph3D (GraphCanvas3D) that lives for the entire
 * lifetime of the view. Switching between modes (knowledge/vault/agent) only
 * swaps data and callback props on the canvas — there is no remount, so the
 * sandboxed webview's single-WebGL-context limit is never exceeded.
 *
 * Each mode is a hook that returns:
 *   - `data` (UnifiedGraphData)        — nodes + links for the canvas
 *   - `filtersBar` (ReactNode)         — top-area filters/toolbar widgets
 *   - `sidebar` (ReactNode)            — left-pane content (detail cards, etc.)
 *   - `onNodeClick(node)`              — handler for click events
 *   - `contextMenu(node)?`             — items for right-click menu
 *   - `empty?` (ReactNode)             — empty-state overlay
 *   - `refresh?`                       — re-fetch button handler
 */

import { useCallback, useMemo, useRef, useState } from "react";
import "../KnowledgeView.css";
import "../GraphView.css";
import "../AgentGraphView.css";
import "./UnifiedGraph.css";
import { GraphCanvas3D } from "./GraphCanvas3D";
import { useKnowledgeMode } from "./modes/knowledge";
import { useVaultMode } from "./modes/vault";
import { useAgentMode } from "./modes/agent";
import { TopEntitiesPopup } from "./widgets/TopEntitiesPopup";
import { WebGLBoundary, WebGLFallback, probeWebGL } from "./WebGLBoundary";
import type { ContextMenuItem, GraphCanvasHandle, ModeId, UnifiedNode } from "./types";

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
  graphSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onGraphSourceFilterHandled?: () => void;
  onViewEntityGraph?: (path: string) => void;
  onStartGraphIndex?: (path: string) => void;
  onSpawnSession?: (entityId: number, entityName: string) => void;
}

const TABS: { id: ModeId; label: string }[] = [
  { id: "knowledge", label: "Knowledge" },
  { id: "vault", label: "Vault" },
  { id: "agent", label: "Agent" },
];

export default function UnifiedGraph({
  onOpenSkill,
  onSelectSession,
  graphSourceFilter,
  onGraphSourceFilterHandled,
  onViewEntityGraph,
  onStartGraphIndex,
  onSpawnSession,
}: Props) {
  const [mode, setMode] = useState<ModeId>("knowledge");
  const [graphSearch, setGraphSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ node: UnifiedNode; items: ContextMenuItem[]; x: number; y: number } | null>(null);
  const [showTopEntities, setShowTopEntities] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  const canvasRef = useRef<GraphCanvasHandle | null>(null);
  // Probe once on mount. If the host can't give us a WebGL context, render
  // the fallback instead of letting react-force-graph-3d crash the tree.
  const [webglProbe, setWebglProbe] = useState(() => probeWebGL());

  // All three mode hooks are always mounted so their data is fresh, but only
  // the active mode's data drives the canvas. (The hooks are cheap; each
  // does its own fetch on first mount and caches.)
  const knowledge = useKnowledgeMode({
    initialSourceFilter: mode === "knowledge" ? graphSourceFilter : null,
    onSourceFilterHandled: onGraphSourceFilterHandled,
    onViewEntityGraph,
    onStartGraphIndex,
    onSpawnSession,
  });
  const vault = useVaultMode({ onViewEntityGraph });
  const agent = useAgentMode({ onOpenSkill, onSelectSession });

  const active = mode === "knowledge" ? knowledge : mode === "vault" ? vault : agent;

  const handleNodeClick = useCallback((node: UnifiedNode | null) => {
    setSelectedId(node?.id ?? null);
    if (node) active.onNodeClick(node);
  }, [active]);

  const handleNodeRightClick = useCallback((node: UnifiedNode, x: number, y: number) => {
    const items = active.contextMenu?.(node);
    if (!items || items.length === 0) return;
    setContextMenu({ node, items, x, y });
  }, [active]);

  const stats = useMemo(() => ({
    nodes: active.data.nodes.length,
    links: active.data.links.length,
  }), [active.data]);

  // No mode renders a permanent left sidebar — entity detail / vault detail
  // / agent error all float over the canvas. The user can close them to
  // reclaim the canvas area entirely.
  const showSearch = mode === "knowledge";
  const showHopSelector = mode === "knowledge";
  const showTopEntitiesBtn = mode === "knowledge";
  const showRefresh = true;

  const refresh = mode === "vault" ? vault.refresh : mode === "agent" ? agent.refresh : undefined;

  return (
    <div className={`ug-root${fullscreen ? " ug--fullscreen" : ""}`}>
      <div className="ug-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`ug-tab${mode === t.id ? " ug-tab--active" : ""}`}
            onClick={() => { setMode(t.id); setSelectedId(null); setContextMenu(null); setShowTopEntities(false); }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!fullscreen && active.filtersBar && (
        <div className="ug-filters-bar">{active.filtersBar}</div>
      )}

      <div className="ug-main">
        <div className="ug-canvas-wrap">
          {webglProbe.ok ? (
            <WebGLBoundary
              fallback={(reason, retry) => (
                <WebGLFallback
                  reason={reason}
                  nodeCount={stats.nodes}
                  edgeCount={stats.links}
                  onRetry={() => { setWebglProbe(probeWebGL()); retry(); }}
                />
              )}
            >
              <GraphCanvas3D
                ref={canvasRef}
                data={active.data}
                selectedId={selectedId}
                search={graphSearch}
                onSelect={handleNodeClick}
                onNodeRightClick={handleNodeRightClick}
                contextMenu={contextMenu}
                onCloseContextMenu={() => setContextMenu(null)}
                emptyState={active.empty}
              />
            </WebGLBoundary>
          ) : (
            <WebGLFallback
              reason={webglProbe.reason || "WebGL unavailable"}
              nodeCount={stats.nodes}
              edgeCount={stats.links}
              onRetry={() => setWebglProbe(probeWebGL())}
            />
          )}

          {/* Floating toolbar */}
          <div className="ug-toolbar">
            {showSearch && (
              <div className="ug-search">
                <input
                  type="text"
                  className="kv-graph-search-input"
                  placeholder="Find…"
                  value={graphSearch}
                  onChange={(e) => setGraphSearch(e.target.value)}
                />
                {graphSearch && (
                  <button className="kv-graph-search-clear" onClick={() => setGraphSearch("")}>&times;</button>
                )}
              </div>
            )}

            {showHopSelector && mode === "knowledge" && (
              <div className="kv-hop-selector">
                {[1, 2, 3].map((h) => (
                  <button
                    key={h}
                    className={`kv-hop-btn${knowledge.hopDepth === h ? " kv-hop-btn--active" : ""}`}
                    onClick={() => knowledge.setHopDepth(h)}
                    title={`${h}-hop neighborhood`}
                  >
                    {h}
                  </button>
                ))}
              </div>
            )}

            {showTopEntitiesBtn && (
              <button
                className={`ug-tool-btn${showTopEntities ? " ug-tool-btn--active" : ""}`}
                onClick={() => setShowTopEntities((v) => !v)}
                title="Top entities"
              >
                Top entities
              </button>
            )}

            <span className="ug-stat">{stats.nodes} nodes</span>
            <span className="ug-stat">{stats.links} edges</span>

            <button
              className="ug-tool-btn ug-tool-btn--hint"
              title="Drag = rotate · Right-drag = pan · Scroll = zoom"
            >
              ?
            </button>
            <button className="ug-tool-btn" onClick={() => canvasRef.current?.reheat()} title="Re-energize layout (r)">↻</button>
            <button className="ug-tool-btn" onClick={() => canvasRef.current?.zoomIn()} title="Zoom in">+</button>
            <button className="ug-tool-btn" onClick={() => canvasRef.current?.zoomOut()} title="Zoom out">−</button>
            <button className="ug-tool-btn" onClick={() => canvasRef.current?.fit()} title="Fit to view (f)">⛶</button>
            {showRefresh && refresh && (
              <button className="ug-tool-btn" onClick={refresh} title="Refresh data">⟳</button>
            )}
            <button className="ug-tool-btn" onClick={() => setFullscreen((v) => !v)} title={fullscreen ? "Exit full view" : "Full view"}>
              {fullscreen ? "▣" : "▢"}
            </button>
          </div>

          {showTopEntities && mode === "knowledge" && (
            <TopEntitiesPopup
              entities={knowledge.topEntities}
              typeFilter={knowledge.typeFilter}
              onPick={knowledge.onPickEntity}
              onClose={() => setShowTopEntities(false)}
            />
          )}

          {active.sidebar}
        </div>
      </div>
    </div>
  );
}
