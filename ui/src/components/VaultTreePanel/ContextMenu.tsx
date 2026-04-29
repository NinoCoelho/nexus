// Sub-component for VaultTreePanel: right-click context menu.

import type { TreeNode } from "./types";

interface ContextMenuProps {
  node: TreeNode;
  x: number;
  y: number;
  onRename: (node: TreeNode) => void;
  onCtxUpload: (dirPath: string) => void;
  onNewFile: (dirPath: string) => void;
  onNewFolder: (dirPath: string) => void;
  onNewKanban: (dirPath: string) => void;
  onDispatchFile: (filePath: string) => void;
  onDelete: (node: TreeNode) => void;
  /** Only present when vault history is enabled. */
  onUndo?: (node: TreeNode) => void;
  onViewEntityGraph?: (mode: "file" | "folder", path: string) => void;
  onVisualizeFolderGraph?: (path: string) => void;
  onClose: () => void;
}

export function ContextMenu({
  node,
  x,
  y,
  onRename,
  onCtxUpload,
  onNewFile,
  onNewFolder,
  onNewKanban,
  onDispatchFile,
  onDelete,
  onUndo,
  onViewEntityGraph,
  onVisualizeFolderGraph,
  onClose,
}: ContextMenuProps) {
  return (
    <div
      className="vault-context-menu"
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
    >
      <button className="vault-ctx-item" onClick={() => onRename(node)}>Rename</button>
      {node.type === "dir" && (
        <>
          <button className="vault-ctx-item" onClick={() => onCtxUpload(node.path)}>Upload files here</button>
          <button className="vault-ctx-item" onClick={() => onNewFile(node.path)}>New file</button>
          <button className="vault-ctx-item" onClick={() => onNewFolder(node.path)}>New folder</button>
          <button className="vault-ctx-item" onClick={() => onNewKanban(node.path)}>New kanban</button>
          {onViewEntityGraph && (
            <button className="vault-ctx-item" onClick={() => { onViewEntityGraph("folder", node.path); onClose(); }}>
              Entity graph for folder
            </button>
          )}
          {onVisualizeFolderGraph && (
            <button className="vault-ctx-item" onClick={() => { onVisualizeFolderGraph(node.path); onClose(); }}>
              Visualize as graph
            </button>
          )}
        </>
      )}
      {node.type === "file" && node.path.endsWith(".md") && (
        <>
          <button className="vault-ctx-item" onClick={() => onDispatchFile(node.path)}>
            Start chat with this file
          </button>
          {onViewEntityGraph && (
            <button className="vault-ctx-item" onClick={() => { onViewEntityGraph("file", node.path); onClose(); }}>
              Entity graph for file
            </button>
          )}
        </>
      )}
      {onUndo && (
        <button className="vault-ctx-item" onClick={() => onUndo(node)}>
          Undo last change
        </button>
      )}
      <button className="vault-ctx-item vault-ctx-item--danger" onClick={() => onDelete(node)}>Delete</button>
    </div>
  );
}
