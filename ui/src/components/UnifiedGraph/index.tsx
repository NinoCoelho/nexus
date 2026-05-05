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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "../KnowledgeView.css";
import "../GraphView.css";
import "../AgentGraphView.css";
import "./UnifiedGraph.css";
import { GraphCanvas3D } from "./GraphCanvas3D";
import { useKnowledgeMode } from "./modes/knowledge";
import { useVaultMode } from "./modes/vault";
import { useAgentMode } from "./modes/agent";
import { useFolderKnowledgeMode } from "./modes/folderKnowledge";
import { FolderGraphTab } from "../FolderGraph/FolderGraphTab";
import {
  getFolderTabs,
  setFolderTabs,
  type FolderTab,
} from "../../api/folderGraph";
import { WebGLBoundary, WebGLFallback, probeWebGL } from "./WebGLBoundary";
import type { ContextMenuItem, GraphCanvasHandle, ModeId, UnifiedNode } from "./types";

/** "vault" = the global GraphRAG knowledge graph. Anything else = a folder path. */
type KnowledgeTab = "vault" | string;

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
  graphSourceFilter?: { mode: "file" | "folder"; path: string } | null;
  onGraphSourceFilterHandled?: () => void;
  pendingFolderGraph?: string | null;
  onPendingFolderGraphHandled?: () => void;
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
  pendingFolderGraph,
  onPendingFolderGraphHandled,
  onViewEntityGraph,
  onStartGraphIndex,
  onSpawnSession,
}: Props) {
  const [mode, setMode] = useState<ModeId>("knowledge");
  const [knowledgeTab, setKnowledgeTab] = useState<KnowledgeTab>("vault");
  const [folderTabs, setFolderTabsState] = useState<FolderTab[]>([]);
  // Bumped to force the per-folder hook to refetch after a reindex completes.
  const [folderRefreshKey, setFolderRefreshKey] = useState(0);
  // Bumped to forward toolbar Edit/Reindex actions to the active FolderGraphTab.
  const [folderEditTrigger, setFolderEditTrigger] = useState(0);
  const [folderReindexTrigger, setFolderReindexTrigger] = useState(0);
  const [folderResetTrigger, setFolderResetTrigger] = useState(0);

  const [graphSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ node: UnifiedNode; items: ContextMenuItem[]; x: number; y: number } | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");

  const canvasRef = useRef<GraphCanvasHandle | null>(null);
  // Probe once on mount. If the host can't give us a WebGL context, render
  // the fallback instead of letting react-force-graph-3d crash the tree.
  const [webglProbe, setWebglProbe] = useState(() => probeWebGL());

  // Restore persisted folder tabs on mount.
  useEffect(() => {
    getFolderTabs().then(setFolderTabsState).catch(() => {});
  }, []);

  // Handle a "Visualize as graph" click coming from the vault tree:
  // ensure the folder tab exists, switch to it, and bring this view forward.
  useEffect(() => {
    if (!pendingFolderGraph) return;
    const path = pendingFolderGraph;
    setMode("knowledge");
    setKnowledgeTab(path);
    setFolderTabsState((prev) => {
      if (prev.some((t) => t.path === path)) return prev;
      const label = path.split("/").pop() || path;
      const next = [...prev, { path, label }];
      setFolderTabs(next).catch(() => {});
      return next;
    });
    onPendingFolderGraphHandled?.();
  }, [pendingFolderGraph, onPendingFolderGraphHandled]);

  // All three mode hooks are always mounted so their data is fresh, but only
  // the active mode's data drives the canvas. (The hooks are cheap; each
  // does its own fetch on first mount and caches.)
  const knowledge = useKnowledgeMode({
    initialSourceFilter: mode === "knowledge" && knowledgeTab === "vault" ? graphSourceFilter : null,
    onSourceFilterHandled: onGraphSourceFilterHandled,
    onViewEntityGraph,
    onStartGraphIndex,
    onSpawnSession,
  });
  const vault = useVaultMode({ onViewEntityGraph });
  const agent = useAgentMode({ onOpenSkill, onSelectSession });

  // The folder-knowledge hook is keyed on the active folder path; we only
  // mount it when the inner Knowledge sub-tab is a folder. A `key=` keeps the
  // hook state isolated per-folder so switching tabs swaps data cleanly.
  const folderActive = mode === "knowledge" && knowledgeTab !== "vault";
  const folderKnowledge = useFolderKnowledgeMode({
    path: folderActive ? knowledgeTab : "",
    refreshKey: folderRefreshKey,
  });

  const active =
    mode === "knowledge"
      ? (folderActive ? folderKnowledge : knowledge)
      : mode === "vault"
      ? vault
      : agent;

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

  const showHopSelector = mode === "knowledge" && !folderActive;
  const showRefresh = true;

  const refresh =
    mode === "vault"
      ? vault.refresh
      : mode === "agent"
      ? agent.refresh
      : folderActive
      ? folderKnowledge.refresh
      : undefined;

  // The "search" we feed to the canvas (for highlight + pulse) mirrors the
  // semantic-search input in knowledge mode, so a single text box drives
  // both the GraphRAG query AND the visual highlight. Vault/agent modes
  // fall back to the local graphSearch state. Folder tabs have no search yet.
  const canvasSearch =
    mode === "knowledge" && !folderActive ? (knowledge.queryText ?? "") : graphSearch;

  function closeFolderTab(path: string) {
    setFolderTabsState((prev) => {
      const next = prev.filter((t) => t.path !== path);
      setFolderTabs(next).catch(() => {});
      return next;
    });
    if (knowledgeTab === path) setKnowledgeTab("vault");
  }

  // `/` opens the in-graph find widget. Esc closes it (or exits fullscreen
  // if the widget is already closed). The main "Search your knowledge"
  // input keeps its own behavior (typing it pulses matches softly white).
  const findInputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (document.activeElement as HTMLElement | null)?.tagName;
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && tag !== "INPUT" && tag !== "TEXTAREA") {
        e.preventDefault();
        setFindOpen(true);
        // Focus runs after render; tiny timeout lets the input mount.
        setTimeout(() => findInputRef.current?.focus(), 0);
      } else if (e.key === "Escape") {
        if (findOpen) {
          setFindOpen(false);
          setFindQuery("");
        } else if (fullscreen) {
          setFullscreen(false);
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [fullscreen, findOpen]);

  // When findQuery changes, debounce-fly to the nearest match so the user
  // sees their target rotate into view. Skips if zero matches.
  useEffect(() => {
    if (!findOpen) return;
    const term = findQuery.trim().toLowerCase();
    if (!term) return;
    const handle = setTimeout(() => {
      const matches = active.data.nodes
        .filter((n) => n.label.toLowerCase().includes(term))
        .map((n) => n.id);
      if (matches.length > 0) canvasRef.current?.flyToNearestMatch(matches);
    }, 350);
    return () => clearTimeout(handle);
  }, [findQuery, findOpen, active.data.nodes]);

  const findMatchCount = useMemo(() => {
    const term = findQuery.trim().toLowerCase();
    if (!term) return 0;
    return active.data.nodes.filter((n) => n.label.toLowerCase().includes(term)).length;
  }, [findQuery, active.data.nodes]);

  // Toolbar — always rendered (even in fullscreen) so the user can always
  // exit. Lives in the top-right of .ug-root and stays visible across modes.
  const toolbar = (
    <div className="ug-toolbar">
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

      <span className="ug-stat">{stats.nodes}n</span>
      <span className="ug-stat">{stats.links}e</span>

      {folderActive && (
        <>
          <button
            className="ug-tool-btn"
            onClick={() => setFolderEditTrigger((v) => v + 1)}
            title="Edit ontology"
          >
            ⚙
          </button>
          <button
            className="ug-tool-btn"
            onClick={() => setFolderReindexTrigger((v) => v + 1)}
            title="Rebuild graph from scratch"
          >
            ⟳
          </button>
          <button
            className="ug-tool-btn"
            onClick={() => setFolderResetTrigger((v) => v + 1)}
            title="Delete graph and start over"
          >
            ✕
          </button>
        </>
      )}

      <button
        className={`ug-tool-btn${findOpen ? " ug-tool-btn--active" : ""}`}
        onClick={() => {
          setFindOpen((v) => !v);
          if (!findOpen) setTimeout(() => findInputRef.current?.focus(), 0);
        }}
        title="Find in graph (/)"
      >
        🔍
      </button>
      <button className="ug-tool-btn ug-tool-btn--hint" title="Drag = rotate · Right-drag = pan · Scroll = zoom · / = find in graph · Esc = close find / exit full view">?</button>
      <button className="ug-tool-btn" onClick={() => canvasRef.current?.reheat()} title="Re-energize layout (r)">↻</button>
      <button className="ug-tool-btn" onClick={() => canvasRef.current?.zoomIn()} title="Zoom in">+</button>
      <button className="ug-tool-btn" onClick={() => canvasRef.current?.zoomOut()} title="Zoom out">−</button>
      <button className="ug-tool-btn" onClick={() => canvasRef.current?.fit()} title="Fit to view (f)">⛶</button>
      {showRefresh && refresh && (
        <button className="ug-tool-btn" onClick={refresh} title="Refresh data">⟳</button>
      )}
      <button
        className={`ug-tool-btn${fullscreen ? " ug-tool-btn--active" : ""}`}
        onClick={() => setFullscreen((v) => !v)}
        title={fullscreen ? "Exit full view (Esc)" : "Full view"}
      >
        {fullscreen ? "✕" : "▢"}
      </button>
    </div>
  );

  return (
    <div className={`ug-root${fullscreen ? " ug--fullscreen" : ""}`}>
      <div className="ug-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`ug-tab${mode === t.id ? " ug-tab--active" : ""}`}
            onClick={() => { setMode(t.id); setSelectedId(null); setContextMenu(null); }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {mode === "knowledge" && (folderTabs.length > 0 || folderActive) && (
        <div className="ug-subtabs">
          <button
            key="vault"
            className={`ug-subtab${knowledgeTab === "vault" ? " ug-subtab--active" : ""}`}
            onClick={() => { setKnowledgeTab("vault"); setSelectedId(null); setContextMenu(null); }}
            title="Global vault knowledge graph"
          >
            Vault
          </button>
          {folderTabs.map((t) => (
            <span key={t.path} className={`ug-subtab-wrap${knowledgeTab === t.path ? " ug-subtab-wrap--active" : ""}`}>
              <button
                className={`ug-subtab${knowledgeTab === t.path ? " ug-subtab--active" : ""}`}
                onClick={() => { setKnowledgeTab(t.path); setSelectedId(null); setContextMenu(null); }}
                title={t.path}
              >
                {t.label}
              </button>
              <button
                className="ug-subtab-close"
                onClick={(e) => { e.stopPropagation(); closeFolderTab(t.path); }}
                title="Close tab (keeps the .nexus-graph index on disk)"
                aria-label={`Close ${t.label}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {!fullscreen && active.filtersBar && (
        <div className="ug-filters-bar">
          {active.filtersBar}
        </div>
      )}

      {toolbar}

      {findOpen && (
        <div className="ug-find-widget" role="dialog" aria-label="Find in graph">
          <span className="ug-find-icon">/</span>
          <input
            ref={findInputRef}
            className="ug-find-input"
            type="text"
            placeholder="Find in graph…"
            value={findQuery}
            onChange={(e) => setFindQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const term = findQuery.trim().toLowerCase();
                if (!term) return;
                const matches = active.data.nodes
                  .filter((n) => n.label.toLowerCase().includes(term))
                  .map((n) => n.id);
                if (matches.length > 0) canvasRef.current?.flyToNearestMatch(matches);
              }
            }}
          />
          <span className="ug-find-count">{findMatchCount}</span>
          <button
            className="ug-find-close"
            onClick={() => { setFindOpen(false); setFindQuery(""); }}
            title="Close (Esc)"
            aria-label="Close find widget"
          >
            ×
          </button>
        </div>
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
                search={canvasSearch}
                findQuery={findOpen ? findQuery : ""}
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

          {active.sidebar}

          {folderActive && (
            <FolderGraphTab
              key={knowledgeTab}
              folderPath={knowledgeTab}
              folderLabel={
                folderTabs.find((t) => t.path === knowledgeTab)?.label
                ?? knowledgeTab.split("/").pop()
                ?? knowledgeTab
              }
              onReindexComplete={() => setFolderRefreshKey((v) => v + 1)}
              externalEditOntology={folderEditTrigger}
              externalReindex={folderReindexTrigger}
              externalReset={folderResetTrigger}
            />
          )}
        </div>
      </div>
    </div>
  );
}
