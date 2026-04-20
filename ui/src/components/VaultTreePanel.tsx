import { useCallback, useEffect, useRef, useState } from "react";
import "./VaultView.css";
import {
  deleteVaultFile,
  getVaultTag,
  getVaultTags,
  getVaultTree,
  postVaultFolder,
  putVaultFile,
  reindexVault,
  searchVault,
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
}

function TreeItem({ node, depth, selectedPath, onSelect, onContextMenu }: TreeItemProps) {
  const [open, setOpen] = useState(true);
  const isActive = node.path === selectedPath;

  return (
    <div>
      <button
        className={`vault-tree-row${isActive ? " vault-tree-row--active" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => {
          if (node.type === "dir") setOpen((o) => !o);
          else onSelect(node.path);
        }}
        onContextMenu={(e) => onContextMenu(e, node)}
      >
        <span className="vault-tree-icon">
          {node.type === "dir" ? <FolderIcon open={open} /> : <FileIcon />}
        </span>
        <span className="vault-tree-name">{node.name}</span>
      </button>
      {node.type === "dir" && open && node.children?.map((child) => (
        <TreeItem
          key={child.path}
          node={child}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
          onContextMenu={onContextMenu}
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
}

export default function VaultTreePanel({
  selectedPath,
  onSelectPath,
  openPath,
  onOpenPathHandled,
  onTreeChange,
}: VaultTreePanelProps) {
  const toast = useToast();
  const [rawNodes, setRawNodes] = useState<VaultNode[]>([]);
  const [treeError, setTreeError] = useState(false);

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

  const handleNewFile = async (dirPath?: string) => {
    const name = prompt("File name:", "untitled.md");
    if (!name) return;
    const path = dirPath ? `${dirPath}/${name}` : name;
    try {
      await putVaultFile(path, "");
      refreshTree();
      onSelectPath(path);
    } catch { /* ignore */ }
    setCtxMenu(null);
  };

  const handleNewFolder = async (parentPath?: string) => {
    const name = prompt("Folder name:");
    if (!name) return;
    const path = parentPath ? `${parentPath}/${name}` : name;
    try {
      await postVaultFolder(path);
      refreshTree();
    } catch { /* ignore */ }
    setCtxMenu(null);
  };

  const handleDelete = async (node: TreeNode) => {
    if (!confirm(`Delete "${node.name}"?`)) return;
    try {
      await deleteVaultFile(node.path);
      refreshTree();
      if (selectedPath === node.path) onSelectPath(null);
      onTreeChange?.();
    } catch { /* ignore */ }
    setCtxMenu(null);
  };

  const tree = buildTree(rawNodes);

  return (
    <div className="vault-tree vault-tree--sidebar">
      <div className="vault-tree-header">
        <span className="vault-tree-title">Files</span>
        <button className="vault-tree-add-btn" onClick={() => void handleNewFile()} title="New file">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="10" y1="4" x2="10" y2="16" /><line x1="4" y1="10" x2="16" y2="10" />
          </svg>
        </button>
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

      {tags.length > 0 && !searchQuery && (
        <div className="vault-tag-cloud">
          {tags.map((t) => {
            const maxCount = tags[0].count || 1;
            const scale = 0.75 + 0.5 * (t.count / maxCount);
            return (
              <button
                key={t.tag}
                className={`vault-tag-pill${activeTag === t.tag ? " vault-tag-pill--active" : ""}`}
                style={{ fontSize: `${Math.round(scale * 11)}px` }}
                onClick={() => handleTagClick(t.tag)}
                title={`${t.count} file${t.count !== 1 ? "s" : ""}`}
              >
                #{t.tag}
              </button>
            );
          })}
        </div>
      )}

      {treeError && <div className="vault-tree-error">Couldn&apos;t load — is the server running?</div>}

      {searchQuery ? (
        <div className="vault-search-results">
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
          {ctxMenu.node.type === "dir" && (
            <>
              <button className="vault-ctx-item" onClick={() => void handleNewFile(ctxMenu.node.path)}>New file</button>
              <button className="vault-ctx-item" onClick={() => void handleNewFolder(ctxMenu.node.path)}>New folder</button>
            </>
          )}
          <button className="vault-ctx-item vault-ctx-item--danger" onClick={() => void handleDelete(ctxMenu.node)}>Delete</button>
        </div>
      )}
    </div>
  );
}
