// Sub-component for VaultTreePanel: right-click context menu.

import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("vault");
  return (
    <div
      className="vault-context-menu"
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
    >
      <button className="vault-ctx-item" onClick={() => onRename(node)}>{t("vault:contextMenu.rename")}</button>
      {node.type === "dir" && (
        <>
          <button className="vault-ctx-item" onClick={() => onCtxUpload(node.path)}>{t("vault:contextMenu.uploadHere")}</button>
          <button className="vault-ctx-item" onClick={() => onNewFile(node.path)}>{t("vault:contextMenu.newFile")}</button>
          <button className="vault-ctx-item" onClick={() => onNewFolder(node.path)}>{t("vault:contextMenu.newFolder")}</button>
          <button className="vault-ctx-item" onClick={() => onNewKanban(node.path)}>{t("vault:contextMenu.newKanban")}</button>
          {onViewEntityGraph && (
            <button className="vault-ctx-item" onClick={() => { onViewEntityGraph("folder", node.path); onClose(); }}>
              {t("vault:contextMenu.entityGraphFolder")}
            </button>
          )}
          {onVisualizeFolderGraph && (
            <button className="vault-ctx-item" onClick={() => { onVisualizeFolderGraph(node.path); onClose(); }}>
              {t("vault:contextMenu.visualizeAsGraph")}
            </button>
          )}
        </>
      )}
      {node.type === "file" && node.path.endsWith(".md") && (
        <>
          <button className="vault-ctx-item" onClick={() => onDispatchFile(node.path)}>
            {t("vault:contextMenu.startChatWithFile")}
          </button>
          {onViewEntityGraph && (
            <button className="vault-ctx-item" onClick={() => { onViewEntityGraph("file", node.path); onClose(); }}>
              {t("vault:contextMenu.entityGraphFile")}
            </button>
          )}
        </>
      )}
      {onUndo && (
        <button className="vault-ctx-item" onClick={() => onUndo(node)}>
          {t("vault:contextMenu.undoLastChange")}
        </button>
      )}
      <button className="vault-ctx-item vault-ctx-item--danger" onClick={() => onDelete(node)}>{t("vault:contextMenu.delete")}</button>
    </div>
  );
}
