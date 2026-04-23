import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Modal, { type ModalProps } from "./Modal";
import "./VaultView.css";
import {
  createVaultKanban,
  deleteVaultFile,
  dispatchFromVault,
  getVaultTag,
  getVaultTags,
  getVaultTree,
  postVaultFolder,
  postVaultMove,
  putVaultFile,
  reindexVault,
  searchVault,
  uploadVaultFiles,
  type VaultNode,
  type VaultSearchResult,
  type VaultTagCount,
} from "../api";
import { useToast } from "../toast/ToastProvider";

// ── Tree helpers ──────────────────────────────────────────────────────────────

interface TreeNode {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: TreeNode[];
}

function buildTree(nodes: VaultNode[]): TreeNode[] {
  const root: TreeNode[] = [];
  const map = new Map<string, TreeNode>();

  const sorted = [...nodes].sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    return a.path.localeCompare(b.path);
  });

  for (const n of sorted) {
    const parts = n.path.split("/");
    const name = parts[parts.length - 1];
    const node: TreeNode = { name, path: n.path, type: n.type, children: n.type === "dir" ? [] : undefined };
    map.set(n.path, node);
    if (parts.length === 1) {
      root.push(node);
    } else {
      const parentPath = parts.slice(0, -1).join("/");
      const parent = map.get(parentPath);
      if (parent?.children) {
        parent.children.push(node);
      } else {
        root.push(node);
      }
    }
  }
  return root;
}

// ── TreeItem ──────────────────────────────────────────────────────────────────

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {open
        ? <path d="M2 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6z" />
        : <path d="M2 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6z" />
      }
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
      <polyline points="12 2 12 6 16 6" />
    </svg>
  );
}

interface TreeItemProps {
  node: TreeNode;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onContextMenu: (e: React.MouseEvent, node: TreeNode) => void;
  onMove: (from: string, toDir: string) => void;
  expandedDirs: Set<string>;
  onToggleDir: (path: string) => void;
}

function TreeItem({
  node,
  depth,
  selectedPath,
  onSelect,
  onContextMenu,
  onMove,
  expandedDirs,
  onToggleDir,
}: TreeItemProps) {
  const [dropOver, setDropOver] = useState(false);
  const isActive = node.path === selectedPath;
  const isOpen = expandedDirs.has(node.path);

  const handleClick = () => {
    if (node.type === "dir") {
      if (isActive) {
        onToggleDir(node.path);
      } else {
        onSelect(node.path);
      }
    } else {
      onSelect(node.path);
    }
  };

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData("text/plain", node.path);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent) => {
    if (node.type === "dir") {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setDropOver(true);
    }
  };

  const handleDragLeave = () => setDropOver(false);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDropOver(false);
    const src = e.dataTransfer.getData("text/plain");
    if (src && src !== node.path && node.type === "dir") {
      onMove(src, node.path);
    }
  };

  return (
    <div>
      <button
        className={`vault-tree-row${isActive ? " vault-tree-row--active" : ""}${dropOver ? " vault-tree-row--drop" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={handleClick}
        onContextMenu={(e) => onContextMenu(e, node)}
        draggable
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <span className="vault-tree-icon">
          {node.type === "dir" ? <FolderIcon open={isOpen} /> : <FileIcon />}
        </span>
        <span className="vault-tree-name">{node.name}</span>
      </button>
      {node.type === "dir" && isOpen && node.children?.map((child) => (
        <TreeItem
          key={child.path}
          node={child}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
          onContextMenu={onContextMenu}
          onMove={onMove}
          expandedDirs={expandedDirs}
          onToggleDir={onToggleDir}
        />
      ))}
    </div>
  );
}

// ── Snippet renderer ──────────────────────────────────────────────────────────

type SnippetSegment = { text: string; highlight: boolean };

function parseSnippet(snippet: string): SnippetSegment[] {
  const segments: SnippetSegment[] = [];
  const re = /<mark>(.*?)<\/mark>/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(snippet)) !== null) {
    if (m.index > last) segments.push({ text: snippet.slice(last, m.index), highlight: false });
    segments.push({ text: m[1], highlight: true });
    last = m.index + m[0].length;
  }
  if (last < snippet.length) segments.push({ text: snippet.slice(last), highlight: false });
  return segments;
}

function SnippetText({ snippet }: { snippet: string }) {
  const segs = parseSnippet(snippet);
  return (
    <span>
      {segs.map((s, i) =>
        s.highlight
          ? <mark key={i}>{s.text}</mark>
          : <span key={i}>{s.text}</span>
      )}
    </span>
  );
}

// ── VaultTreePanel ────────────────────────────────────────────────────────────

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
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => new Set());

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<VaultSearchResult[]>([]);
  const [reindexMsg, setReindexMsg] = useState<string | null>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Tag cloud state
  const [tags, setTags] = useState<VaultTagCount[]>([]);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [tagFiles, setTagFiles] = useState<string[]>([]);

  // Context menu
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; node: TreeNode } | null>(null);

  // In-app modal (replaces browser prompt/confirm)
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

  // Load tag cloud
  const refreshTags = useCallback(() => {
    getVaultTags()
      .then((t) => setTags(t.slice(0, 15)))
      .catch(() => { /* silent — tags are non-critical */ });
  }, []);

  useEffect(() => { refreshTags(); }, [refreshTags]);

  // Tag click: toggle filter
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

  const handleMove = useCallback(async (fromPath: string, toDir: string) => {
    const name = fromPath.split("/").pop() ?? fromPath;
    const toPath = `${toDir}/${name}`;
    if (fromPath === toPath) return;
    try {
      await postVaultMove(fromPath, toPath);
      refreshTree();
      if (selectedPath === fromPath) onSelectPath(toPath);
      onTreeChange?.();
    } catch (e) {
      toast.error("Move failed", { detail: e instanceof Error ? e.message : undefined });
    }
  }, [selectedPath, onSelectPath, refreshTree, onTreeChange, toast]);

  const handleRename = useCallback((node: TreeNode) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: "Rename",
      defaultValue: node.name,
      confirmLabel: "Rename",
      onCancel: () => setModal(null),
      onSubmit: async (newName) => {
        setModal(null);
        const parentParts = node.path.split("/");
        parentParts[parentParts.length - 1] = newName;
        const toPath = parentParts.join("/");
        if (toPath === node.path) return;
        try {
          await postVaultMove(node.path, toPath);
          refreshTree();
          if (selectedPath === node.path) onSelectPath(toPath);
          onTreeChange?.();
        } catch (e) {
          toast.error("Rename failed", { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  }, [selectedPath, onSelectPath, refreshTree, onTreeChange, toast]);

  const handleCtxUpload = useCallback((dirPath: string) => {
    setCtxMenu(null);
    uploadCtxDirRef.current?.setAttribute("data-dest-dir", dirPath);
    uploadCtxDirRef.current?.click();
  }, []);

  // Close context menu
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

  const handleNewFile = (dirPath?: string) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: "New file",
      message: dirPath ? `Creating in ${dirPath}/` : undefined,
      defaultValue: "untitled.md",
      confirmLabel: "Create",
      onCancel: () => setModal(null),
      onSubmit: async (name) => {
        setModal(null);
        const path = dirPath ? `${dirPath}/${name}` : name;
        try {
          await putVaultFile(path, "");
          refreshTree();
          onSelectPath(path);
        } catch (e) {
          toast.error("Couldn't create file", { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  };

  const handleNewFolder = (parentPath?: string) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: "New folder",
      message: parentPath ? `Creating in ${parentPath}/` : undefined,
      placeholder: "folder-name",
      confirmLabel: "Create",
      onCancel: () => setModal(null),
      onSubmit: async (name) => {
        setModal(null);
        const path = parentPath ? `${parentPath}/${name}` : name;
        try {
          await postVaultFolder(path);
          refreshTree();
        } catch (e) {
          toast.error("Couldn't create folder", { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  };

  const handleNewKanban = (dirPath?: string) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: "New kanban",
      message: dirPath ? `Creating in ${dirPath}/` : undefined,
      defaultValue: "board.md",
      confirmLabel: "Create",
      onCancel: () => setModal(null),
      onSubmit: async (name) => {
        setModal(null);
        const filename = name.endsWith(".md") ? name : `${name}.md`;
        const path = dirPath ? `${dirPath}/${filename}` : filename;
        try {
          await createVaultKanban(path, { title: filename.replace(/\.md$/, "") });
          refreshTree();
          onSelectPath(path);
        } catch (e) {
          toast.error("Couldn't create kanban", { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  };

  const handleDispatchFile = async (filePath: string) => {
    try {
      const res = await dispatchFromVault({ path: filePath });
      onDispatchToChat?.(res.session_id, res.seed_message);
    } catch (e) {
      toast.error("Couldn't start chat", { detail: e instanceof Error ? e.message : undefined });
    }
    setCtxMenu(null);
  };

  const descendantCounts = useMemo(() => {
    const counts = new Map<string, { files: number; dirs: number }>();
    for (const n of rawNodes) {
      for (const anc of rawNodes) {
        if (anc.type !== "dir" || anc.path === n.path) continue;
        if (n.path.startsWith(anc.path + "/")) {
          const c = counts.get(anc.path) ?? { files: 0, dirs: 0 };
          if (n.type === "file") c.files += 1;
          else c.dirs += 1;
          counts.set(anc.path, c);
        }
      }
    }
    return counts;
  }, [rawNodes]);

  const doDelete = async (path: string, recursive: boolean) => {
    try {
      await deleteVaultFile(path, recursive);
      refreshTree();
      if (selectedPath === path || (recursive && selectedPath?.startsWith(path + "/"))) {
        onSelectPath(null);
      }
      onTreeChange?.();
    } catch (e) {
      toast.error("Delete failed", { detail: e instanceof Error ? e.message : undefined });
    }
  };

  const handleDelete = (node: TreeNode) => {
    setCtxMenu(null);
    if (node.type === "file") {
      setModal({
        kind: "confirm",
        title: "Delete file",
        message: `Delete "${node.name}"? This cannot be undone.`,
        confirmLabel: "Delete",
        danger: true,
        onCancel: () => setModal(null),
        onSubmit: () => { setModal(null); void doDelete(node.path, false); },
      });
      return;
    }
    const counts = descendantCounts.get(node.path);
    const isEmpty = !counts || (counts.files === 0 && counts.dirs === 0);
    if (isEmpty) {
      setModal({
        kind: "confirm",
        title: "Delete folder",
        message: `Delete empty folder "${node.name}"?`,
        confirmLabel: "Delete",
        danger: true,
        onCancel: () => setModal(null),
        onSubmit: () => { setModal(null); void doDelete(node.path, false); },
      });
      return;
    }
    // Non-empty: first confirm, then second confirmation.
    const summary = [
      counts.files > 0 && `${counts.files} file${counts.files === 1 ? "" : "s"}`,
      counts.dirs > 0 && `${counts.dirs} subfolder${counts.dirs === 1 ? "" : "s"}`,
    ].filter(Boolean).join(", ");
    setModal({
      kind: "confirm",
      title: "Delete folder and its contents?",
      message: `"${node.name}" contains ${summary}. All of it will be permanently removed.`,
      confirmLabel: "Continue",
      danger: true,
      onCancel: () => setModal(null),
      onSubmit: () => {
        setModal({
          kind: "prompt",
          title: "Type the folder name to confirm",
          message: `To permanently delete "${node.name}" and its ${summary}, type its name below.`,
          placeholder: node.name,
          confirmLabel: "Delete forever",
          onCancel: () => setModal(null),
          onSubmit: (typed) => {
            if (typed.trim() !== node.name) {
              toast.error("Name didn't match — delete cancelled");
              setModal(null);
              return;
            }
            setModal(null);
            void doDelete(node.path, true);
          },
        });
      },
    });
  };

  const tree = buildTree(rawNodes);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>, overrideDir?: string) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    try {
      const destDir = overrideDir ?? (selectedPath
        ? rawNodes.find((n) => n.path === selectedPath && n.type === "dir")
          ? selectedPath
          : selectedPath.includes("/")
            ? selectedPath.substring(0, selectedPath.lastIndexOf("/"))
            : undefined
        : undefined);
      const result = await uploadVaultFiles(Array.from(fileList), destDir);
      toast.success(`Uploaded ${result.uploaded.length} file${result.uploaded.length === 1 ? "" : "s"}`);
      refreshTree();
      if (result.uploaded.length === 1) onSelectPath(result.uploaded[0].path);
    } catch (err) {
      toast.error("Upload failed", { detail: err instanceof Error ? err.message : undefined });
    }
    e.target.value = "";
  };

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
          <button className="vault-tree-add-btn" onClick={() => void handleNewFolder()} title="New folder">
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6z" />
              <line x1="10" y1="9" x2="10" y2="14" /><line x1="7.5" y1="11.5" x2="12.5" y2="11.5" />
            </svg>
          </button>
          <button className="vault-tree-add-btn" onClick={() => void handleNewFile()} title="New file">
            <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" /><line x1="4" y1="10" x2="16" y2="10" />
            </svg>
          </button>
          <input
            ref={uploadInputRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => void handleUpload(e)}
          />
          <input
            ref={uploadCtxDirRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => {
              const dir = uploadCtxDirRef.current?.getAttribute("data-dest-dir") ?? undefined;
              void handleUpload(e, dir);
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
          {searchResults.map((r) => (
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
              onMove={handleMove}
              expandedDirs={expandedDirs}
              onToggleDir={handleToggleDir}
            />
          ))}
        </div>
      )}

      {/* Context menu */}
      {ctxMenu && (
        <div
          className="vault-context-menu"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button className="vault-ctx-item" onClick={() => handleRename(ctxMenu.node)}>Rename</button>
          {ctxMenu.node.type === "dir" && (
            <>
              <button className="vault-ctx-item" onClick={() => void handleCtxUpload(ctxMenu.node.path)}>Upload files here</button>
              <button className="vault-ctx-item" onClick={() => void handleNewFile(ctxMenu.node.path)}>New file</button>
              <button className="vault-ctx-item" onClick={() => void handleNewFolder(ctxMenu.node.path)}>New folder</button>
              <button className="vault-ctx-item" onClick={() => void handleNewKanban(ctxMenu.node.path)}>New kanban</button>
              {onViewEntityGraph && (
                <button className="vault-ctx-item" onClick={() => { onViewEntityGraph("folder", ctxMenu.node.path); setCtxMenu(null); }}>
                  Entity graph for folder
                </button>
              )}
            </>
          )}
          {ctxMenu.node.type === "file" && ctxMenu.node.path.endsWith(".md") && (
            <>
              <button className="vault-ctx-item" onClick={() => void handleDispatchFile(ctxMenu.node.path)}>
                Start chat with this file
              </button>
              {onViewEntityGraph && (
                <button className="vault-ctx-item" onClick={() => { onViewEntityGraph("file", ctxMenu.node.path); setCtxMenu(null); }}>
                  Entity graph for file
                </button>
              )}
            </>
          )}
          <button className="vault-ctx-item vault-ctx-item--danger" onClick={() => handleDelete(ctxMenu.node)}>Delete</button>
        </div>
      )}

      {modal && <Modal {...modal} />}
    </div>
  );
}
