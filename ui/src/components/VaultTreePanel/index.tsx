/**
 * VaultTreePanel — file/folder tree browser for the vault.
 *
 * Renders an expandable tree of vault files with context menus for:
 *   - Create file / folder
 *   - Rename (inline editing)
 *   - Delete
 *   - Export to chat (dispatch)
 *   - Create kanban board
 *   - View entity graph
 *
 * Drag-and-drop file upload is supported on folders. The tree is built
 * from the flat list returned by GET /vault/tree.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Modal, { type ModalProps } from "../Modal";
import "../VaultView.css";
import {
  getVaultHistoryStatus,
  getVaultTag,
  getVaultTags,
  getVaultTree,
  reindexVault,
  searchVault,
  type VaultNode,
  type VaultSearchResult,
  type VaultTagCount,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import { TreeItem } from "./TreeItem";
import { ContextMenu } from "./ContextMenu";
import { SearchResultsPanel } from "./SearchResultsPanel";
import { TreeHeader } from "./TreeHeader";
import { buildTree, buildDescendantCounts } from "./treeUtils";
import { useVaultActions } from "./useVaultActions";
import type { TreeNode } from "./types";

interface VaultTreePanelProps {
  selectedPath: string | null;
  onSelectPath: (path: string | null) => void;
  openPath?: string | null;
  onOpenPathHandled?: () => void;
  onTreeChange?: () => void;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onViewEntityGraph?: (mode: "file" | "folder", path: string) => void;
  onVisualizeFolderGraph?: (path: string) => void;
}

export default function VaultTreePanel({
  selectedPath,
  onSelectPath,
  openPath,
  onOpenPathHandled,
  onTreeChange,
  onDispatchToChat,
  onViewEntityGraph,
  onVisualizeFolderGraph,
}: VaultTreePanelProps) {
  const { t } = useTranslation("vault");
  const toast = useToast();
  const [rawNodes, setRawNodes] = useState<VaultNode[]>([]);
  const [treeError, setTreeError] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const uploadCtxDirRef = useRef<HTMLInputElement | null>(null);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem("nexus.vault.expandedDirs");
      if (raw) return new Set(JSON.parse(raw) as string[]);
    } catch { /* ignore */ }
    return new Set();
  });

  // Persist expanded dirs across view switches / reloads.
  useEffect(() => {
    try {
      localStorage.setItem("nexus.vault.expandedDirs", JSON.stringify(Array.from(expandedDirs)));
    } catch { /* ignore quota errors */ }
  }, [expandedDirs]);

  // Auto-expand ancestor folders of the selected file so the tree stays in sync.
  useEffect(() => {
    if (!selectedPath || !selectedPath.includes("/")) return;
    const parts = selectedPath.split("/");
    const ancestors: string[] = [];
    for (let i = 1; i < parts.length; i++) ancestors.push(parts.slice(0, i).join("/"));
    setExpandedDirs((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const a of ancestors) {
        if (!next.has(a)) { next.add(a); changed = true; }
      }
      return changed ? next : prev;
    });
  }, [selectedPath]);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<VaultSearchResult[]>([]);
  const [reindexMsg, setReindexMsg] = useState<string | null>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const [tags, setTags] = useState<VaultTagCount[]>([]);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [tagFiles, setTagFiles] = useState<string[]>([]);

  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; node: TreeNode } | null>(null);
  const [modal, setModal] = useState<ModalProps | null>(null);
  const [historyEnabled, setHistoryEnabled] = useState(false);

  // Refresh history-enabled flag whenever the context menu is about to open
  // — cheap, and keeps the "Undo" item in sync with the settings toggle.
  useEffect(() => {
    if (!ctxMenu) return;
    let cancelled = false;
    getVaultHistoryStatus()
      .then((s) => { if (!cancelled) setHistoryEnabled(s.enabled); })
      .catch(() => { if (!cancelled) setHistoryEnabled(false); });
    return () => { cancelled = true; };
  }, [ctxMenu]);

  const refreshTree = useCallback(() => {
    setTreeError(false);
    getVaultTree().then(setRawNodes).catch(() => setTreeError(true));
  }, []);

  useEffect(() => { refreshTree(); }, [refreshTree]);

  useVaultEvents((event) => {
    if (event.type === "vault.indexed" || event.type === "vault.removed") {
      refreshTree();
      refreshTags();
    }
  });

  // React to an external open request (e.g. "Open in Vault" from a preview modal).
  useEffect(() => {
    if (!openPath) return;
    setSearchQuery(""); setSearchResults([]); setActiveTag(null); setTagFiles([]);
    onOpenPathHandled?.();
  }, [openPath, onOpenPathHandled]);

  const refreshTags = useCallback(() => {
    getVaultTags().then((t) => setTags(t.slice(0, 15))).catch(() => {});
  }, []);

  useEffect(() => { refreshTags(); }, [refreshTags]);

  const handleTagClick = useCallback((tag: string) => {
    if (activeTag === tag) {
      setActiveTag(null); setTagFiles([]);
    } else {
      setActiveTag(tag);
      getVaultTag(tag).then((r) => setTagFiles(r.files)).catch(() => setTagFiles([]));
    }
  }, [activeTag]);

  const handleReindex = async () => {
    try {
      const { indexed } = await reindexVault();
      toast.success(t("vault:tree.reindexed", { count: indexed }));
    } catch (e) {
      toast.error(t("vault:tree.reindexFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  };
  void setReindexMsg; // reserved for any legacy inline UI

  const handleToggleDir = useCallback((path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }, []);

  const descendantCounts = useMemo(() => buildDescendantCounts(rawNodes), [rawNodes]);

  const setCtxMenuNull = useCallback(() => setCtxMenu(null), []);
  const setModalTyped = useCallback((m: ModalProps | null) => setModal(m), []);

  const actions = useVaultActions({
    selectedPath, rawNodes, refreshTree, onSelectPath, onTreeChange,
    onDispatchToChat, toast, setModal: setModalTyped, setCtxMenu: setCtxMenuNull,
    descendantCounts, uploadCtxDirRef,
  });

  // Close context menu on any click
  useEffect(() => {
    if (!ctxMenu) return;
    const handler = () => setCtxMenu(null);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [ctxMenu]);

  // Debounced search
  useEffect(() => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    if (!searchQuery.trim()) { setSearchResults([]); return; }
    searchDebounceRef.current = setTimeout(() => {
      searchVault(searchQuery).then(setSearchResults).catch(() => setSearchResults([]));
    }, 250);
    return () => { if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current); };
  }, [searchQuery]);

  // Escape key clears search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && searchQuery) { setSearchQuery(""); setSearchResults([]); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [searchQuery]);

  const handleCtx = (e: React.MouseEvent, node: TreeNode) => {
    e.preventDefault(); e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, node });
  };

  const tree = buildTree(rawNodes);
  const showResultsPanel = !!searchQuery || !!activeTag;

  return (
    <div className="vault-tree vault-tree--sidebar">
      <TreeHeader
        onUploadClick={() => uploadInputRef.current?.click()}
        onNewFolder={() => void actions.handleNewFolder()}
        onNewFile={() => void actions.handleNewFile()}
        uploadInputRef={uploadInputRef}
        uploadCtxDirRef={uploadCtxDirRef}
        onUploadChange={(e) => void actions.handleUpload(e)}
        onCtxUploadChange={(e) => {
          const dir = uploadCtxDirRef.current?.getAttribute("data-dest-dir") ?? undefined;
          void actions.handleUpload(e, dir);
        }}
      />

      {/* Search bar */}
      <div className="vault-search-bar">
        <span className="vault-search-icon">
          <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="8.5" cy="8.5" r="5.5" />
            <line x1="13" y1="13" x2="18" y2="18" />
          </svg>
        </span>
        <input
          ref={searchInputRef}
          className="vault-search-input"
          type="text"
          placeholder={t("vault:tree.searchPlaceholder")}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          spellCheck={false}
        />
        {searchQuery && (
          <button className="vault-search-clear" onClick={() => { setSearchQuery(""); setSearchResults([]); }} title="Clear">×</button>
        )}
        <button className="vault-search-reindex" onClick={() => void handleReindex()} title="Reindex vault">
          <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="1 4 1 10 7 10" />
            <path d="M3.51 15a9 9 0 1 0 .49-3.31" />
          </svg>
        </button>
      </div>
      {reindexMsg && <div className="vault-reindex-toast">{reindexMsg}</div>}

      {treeError && <div className="vault-tree-error">Couldn&apos;t load — is the server running?</div>}

      {showResultsPanel ? (
        <SearchResultsPanel
          searchQuery={searchQuery}
          searchResults={searchResults}
          tags={tags}
          activeTag={activeTag}
          tagFiles={tagFiles}
          selectedPath={selectedPath}
          onSelectPath={(p) => onSelectPath(p)}
          onTagClick={handleTagClick}
          onClearSearch={() => { setSearchQuery(""); setSearchResults([]); }}
        />
      ) : (
        <div className="vault-tree-body">
          {tree.length === 0 && !treeError && <div className="vault-tree-empty">No files yet</div>}
          {tree.map((node) => (
            <TreeItem
              key={node.path}
              node={node}
              depth={0}
              selectedPath={selectedPath}
              onSelect={onSelectPath}
              onContextMenu={handleCtx}
              onMove={actions.handleMove}
              expandedDirs={expandedDirs}
              onToggleDir={handleToggleDir}
              dirCounts={descendantCounts}
            />
          ))}
        </div>
      )}

      {ctxMenu && (
        <ContextMenu
          node={ctxMenu.node}
          x={ctxMenu.x}
          y={ctxMenu.y}
          onRename={actions.handleRename}
          onCtxUpload={actions.handleCtxUpload}
          onNewFile={(p) => void actions.handleNewFile(p)}
          onNewFolder={(p) => void actions.handleNewFolder(p)}
          onNewKanban={(p) => void actions.handleNewKanban(p)}
          onDispatchFile={(p) => void actions.handleDispatchFile(p)}
          onDelete={actions.handleDelete}
          onUndo={historyEnabled ? actions.handleUndo : undefined}
          onViewEntityGraph={onViewEntityGraph}
          onVisualizeFolderGraph={onVisualizeFolderGraph}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {modal && <Modal {...modal} />}
    </div>
  );
}
