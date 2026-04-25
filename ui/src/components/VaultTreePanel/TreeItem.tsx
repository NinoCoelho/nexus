// Sub-component for VaultTreePanel: single tree row with drag-and-drop support.

import { useState } from "react";
import { iconFor, formatBytes, formatRelativeTime } from "../../fileTypes";
import HoverTooltip from "../HoverTooltip";
import type { TreeNode } from "./types";

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

function buildTooltip(node: TreeNode, dirCounts: Map<string, { files: number; dirs: number }>): React.ReactNode {
  if (node.type === "file") {
    const size = formatBytes(node.size);
    const mt = formatRelativeTime(node.mtime);
    return (
      <>
        <div className="hover-tooltip-title">{node.name}</div>
        <div className="hover-tooltip-row"><span className="hover-tooltip-label">Path</span><span>{node.path}</span></div>
        {size && <div className="hover-tooltip-row"><span className="hover-tooltip-label">Size</span><span>{size}</span></div>}
        {mt && <div className="hover-tooltip-row"><span className="hover-tooltip-label">Modified</span><span>{mt}</span></div>}
      </>
    );
  }
  const c = dirCounts.get(node.path) ?? { files: 0, dirs: 0 };
  const parts = [
    c.files > 0 ? `${c.files} file${c.files === 1 ? "" : "s"}` : null,
    c.dirs > 0 ? `${c.dirs} subfolder${c.dirs === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  if (parts.length === 0) return <div className="hover-tooltip-title">{node.name} (empty)</div>;
  return (
    <>
      <div className="hover-tooltip-title">{node.name}</div>
      <div>{parts.join(", ")}</div>
    </>
  );
}

export interface TreeItemProps {
  node: TreeNode;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onContextMenu: (e: React.MouseEvent, node: TreeNode) => void;
  onMove: (from: string, toDir: string) => void;
  expandedDirs: Set<string>;
  onToggleDir: (path: string) => void;
  dirCounts: Map<string, { files: number; dirs: number }>;
}

export function TreeItem({
  node,
  depth,
  selectedPath,
  onSelect,
  onContextMenu,
  onMove,
  expandedDirs,
  onToggleDir,
  dirCounts,
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
      <HoverTooltip content={buildTooltip(node, dirCounts)} delay={2000}>
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
            {node.type === "dir" ? <FolderIcon open={isOpen} /> : iconFor(node.path)}
          </span>
          <span className="vault-tree-name">{node.name}</span>
        </button>
      </HoverTooltip>
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
          dirCounts={dirCounts}
        />
      ))}
    </div>
  );
}
