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
import Modal, { type ModalProps } from "../Modal";
import "../VaultView.css";
import {
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
import { TreeItem } from "./TreeItem";
import { SnippetText } from "./SnippetText";
import { ContextMenu } from "./ContextMenu";
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
}

export default function VaultTreePanel({
  selectedPath,
  onSelectPath,
  openPath,
  onOpenPathHandled,
  onTreeChange,
  onDispatchToChat,
  onViewEntityGraph,
}: VaultTreePanelProps) {
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
      localStorage.setItem(
        "nexus.vault.expandedDirs",
        JSON.stringify(Array.from(expandedDirs)),
      );
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

  const refreshTree = useCallback(() => {
    setTreeError(false);
    getVaultTree()
      .then(setRawNodes)
      .catch(() => setTreeError(true));
  }, []);

  useEffect(() => { refreshTree(); }, [refreshTree]);

  // React to an external open request (e.g. "Open in Vault" from a preview modal).
  useEffect(() => {
    if (!openPath) return;
    setSearchQuery("");
    setSearchResults([]);
    setActiveTag(null);
    setTagFiles([]);
    onOpenPathHandled?.();
  }, [openPath, onOpenPathHandled]);

  const refreshTags = useCallback(() => {
    getVaultTags()
      .then((t) => setTags(t.slice(0, 15)))
      .catch(() => { /* silent — tags are non-critical */ });
  }, []);

  useEffect(() => { refreshTags(); }, [refreshTags]);

  const handleTagClick = useCallback((tag: string) => {
    if (activeTag === tag) {
      setActiveTag(null);
      setTagFiles([]);
    } else {
      setActiveTag(tag);
      getVaultTag(tag)
        .then((r) => setTagFiles(r.files))
        .catch(() => setTagFiles([]));
    }
  }, [activeTag]);

  const handleReindex = async () => {
    try {
      const { indexed } = await reindexVault();
      toast.success(`Indexed ${indexed} file${indexed === 1 ? "" : "s"}`);
    } catch (e) {
      toast.error("Reindex failed", { detail: e instanceof Error ? e.message : undefined });
    }
  };
  void setReindexMsg; // reserved for any legacy inline UI

  const handleToggleDir = useCallback((path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const descendantCounts = useMemo(() => buildDescendantCounts(rawNodes), [rawNodes]);

  const setCtxMenuNull = useCallback(() => setCtxMenu(null), []);
  const setModalTyped = useCallback((m: ModalProps | null) => setModal(m), []);

  const actions = useVaultActions({
    selectedPath,
    rawNodes,
    refreshTree,
    onSelectPath,
    onTreeChange,
    onDispatchToChat,
    toast,
    setModal: setModalTyped,
    setCtxMenu: setCtxMenuNull,
    descendantCounts,
    uploadCtxDirRef,
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
    if (!searchQuery.trim()) {
      setSearchResults([]);
      return;
    }
    searchDebounceRef.current = setTimeout(() => {
      searchVault(searchQuery)
        .then(setSearchResults)
        .catch(() => setSearchResults([]));
    }, 250);
    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, [searchQuery]);

  // Escape key clears search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && searchQuery) {
        setSearchQuery("");
        setSearchResults([]);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [searchQuery]);

  const handleCtx = (e: React.MouseEvent, node: TreeNode) => {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, node });
  };

  const tree = buildTree(rawNodes);

  return (
    <div className="vault-tree vault-tree--sidebar">
      <div className="vault-tree-header">
        <span className="vault-tree-title">Files</span>
        <div className="vault-tree-header-actions">
          <button className="vault-tree-add-btn" onClick={() => uploadInputRef.current?.click()} title="Upload files">
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
              <polyline points="7,8 10,4 13,8" />
              <line x1="10" y1="4" x2="10" y2="14" />
            </svg>
          </button>
          <button className="vault-tree-add-btn" onClick={() => void actions.handleNewFolder()} title="New folder">
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6z" />
              <line x1="10" y1="9" x2="10" y2="14" /><line x1="7.5" y1="11.5" x2="12.5" y2="11.5" />
            </svg>
          </button>
          <button className="vault-tree-add-btn" onClick={() => void actions.handleNewFile()} title="New file">
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" /><line x1="4" y1="10" x2="16" y2="10" />
            </svg>
          </button>
          <input
            ref={uploadInputRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => void actions.handleUpload(e)}
          />
          <input
            ref={uploadCtxDirRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => {
              const dir = uploadCtxDirRef.current?.getAttribute("data-dest-dir") ?? undefined;
              void actions.handleUpload(e, dir);
            }}
          />
        </div>
      </div>

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
          placeholder="Search vault…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          spellCheck={false}
        />
        {searchQuery && (
          <button
            className="vault-search-clear"
            onClick={() => { setSearchQuery(""); setSearchResults([]); }}
            title="Clear"
          >×</button>
        )}
        <button
          className="vault-search-reindex"
          onClick={() => void handleReindex()}
          title="Reindex vault"
        >
          <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="1 4 1 10 7 10" />
            <path d="M3.51 15a9 9 0 1 0 .49-3.31" />
          </svg>
        </button>
      </div>
      {reindexMsg && <div className="vault-reindex-toast">{reindexMsg}</div>}

      {treeError && <div className="vault-tree-error">Couldn&apos;t load — is the server running?</div>}

      {searchQuery ? (
        <div className="vault-search-results">
          {(() => {
            const q = searchQuery.trim().toLowerCase().replace(/^#/, "");
            const suggestions = q
              ? tags.filter((t) => t.tag.toLowerCase().includes(q)).slice(0, 8)
              : [];
            if (!suggestions.length) return null;
            return (
              <div className="vault-tag-suggestions">
                {suggestions.map((t) => (
                  <button
                    key={t.tag}
                    className={`vault-tag-pill${activeTag === t.tag ? " vault-tag-pill--active" : ""}`}
                    onClick={() => {
                      setSearchQuery("");
                      setSearchResults([]);
                      handleTagClick(t.tag);
                    }}
                    title={`${t.count} file${t.count !== 1 ? "s" : ""}`}
                  >
                    #{t.tag}
                  </button>
                ))}
              </div>
            );
          })()}
          {searchResults.length === 0 && (
            <div className="vault-tree-empty">No results</div>
          )}
          {searchResults.map((r: VaultSearchResult) => (
            <button
              key={r.path}
              className={`vault-search-result${r.path === selectedPath ? " vault-tree-row--active" : ""}`}
              onClick={() => { onSelectPath(r.path); }}
            >
              <span className="vault-search-result-path">{r.path}</span>
              <span className="vault-search-snippet"><SnippetText snippet={r.snippet} /></span>
            </button>
          ))}
        </div>
      ) : activeTag ? (
        <div className="vault-search-results">
          {tagFiles.length === 0 && (
            <div className="vault-tree-empty">No files with tag #{activeTag}</div>
          )}
          {tagFiles.map((p) => (
            <button
              key={p}
              className={`vault-search-result${p === selectedPath ? " vault-tree-row--active" : ""}`}
              onClick={() => { onSelectPath(p); }}
            >
              <span className="vault-search-result-path">{p}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="vault-tree-body">
          {tree.length === 0 && !treeError && (
            <div className="vault-tree-empty">No files yet</div>
          )}
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

      {/* Context menu */}
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
          onViewEntityGraph={onViewEntityGraph}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {modal && <Modal {...modal} />}
    </div>
  );
}
