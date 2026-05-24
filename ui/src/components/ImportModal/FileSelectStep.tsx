import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { ImportTreeNode } from "../../api/vault";

interface FileSelectStepProps {
  tree: ImportTreeNode[];
  checkedPaths: Set<string>;
  onCheck: (paths: Set<string>) => void;
  fileCount: number;
  totalSize: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileSelectStep({
  tree,
  checkedPaths,
  onCheck,
  fileCount,
  totalSize,
}: FileSelectStepProps) {
  const { t } = useTranslation("vault");

  const allPaths = useMemo(() => {
    const paths: string[] = [];
    const walk = (nodes: ImportTreeNode[]) => {
      for (const n of nodes) {
        paths.push(n.path);
        if (n.children) walk(n.children);
      }
    };
    walk(tree);
    return paths;
  }, [tree]);

  const allChecked = allPaths.every((p) => checkedPaths.has(p));

  const toggleAll = useCallback(() => {
    if (allChecked) {
      onCheck(new Set());
    } else {
      onCheck(new Set(allPaths));
    }
  }, [allChecked, allPaths, onCheck]);

  const togglePath = useCallback(
    (path: string, node: ImportTreeNode) => {
      const next = new Set(checkedPaths);
      if (node.type === "dir" && node.children) {
        const descendents = _collectPaths(node);
        const allIn = descendents.every((p) => next.has(p));
        if (allIn) {
          for (const p of descendents) next.delete(p);
          next.delete(path);
        } else {
          for (const p of descendents) next.add(p);
          next.add(path);
        }
      } else {
        if (next.has(path)) next.delete(path);
        else next.add(path);
      }
      onCheck(next);
    },
    [checkedPaths, onCheck],
  );

  return (
    <div className="import-file-select">
      <div className="import-file-select-header">
        <button className="import-select-all-btn" onClick={toggleAll}>
          {allChecked ? t("vault:import.deselectAll") : t("vault:import.selectAll")}
        </button>
        <span className="import-file-count">
          {t("vault:import.filesSelected", {
            count: fileCount,
            size: formatSize(totalSize),
          })}
        </span>
      </div>
      <div className="import-file-tree">
        {tree.map((node) => (
          <FileTreeNode
            key={node.path}
            node={node}
            depth={0}
            checkedPaths={checkedPaths}
            onToggle={togglePath}
          />
        ))}
      </div>
    </div>
  );
}

function FileTreeNode({
  node,
  depth,
  checkedPaths,
  onToggle,
}: {
  node: ImportTreeNode;
  depth: number;
  checkedPaths: Set<string>;
  onToggle: (path: string, node: ImportTreeNode) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 1);
  const isChecked = checkedPaths.has(node.path);
  const childPaths = node.children ? _collectPaths(node) : [];
  const childCheckedCount = childPaths.filter((p) => checkedPaths.has(p)).length;
  const indeterminate = childPaths.length > 0 && childCheckedCount > 0 && childCheckedCount < childPaths.length;

  return (
    <div className="import-tree-node">
      <div
        className="import-tree-row"
        style={{ paddingLeft: depth * 20 + 8 }}
        onClick={() => onToggle(node.path, node)}
      >
        {node.type === "dir" && (
          <span
            className="import-tree-expand"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
          >
            {expanded ? "▾" : "▸"}
          </span>
        )}
        <input
          type="checkbox"
          className="import-tree-checkbox"
          checked={isChecked || indeterminate}
          ref={(el) => {
            if (el) el.indeterminate = indeterminate;
          }}
          onChange={() => onToggle(node.path, node)}
          onClick={(e) => e.stopPropagation()}
        />
        <span className={`import-tree-icon import-tree-icon--${node.type}`}>
          {node.type === "dir" ? "📁" : _fileIcon(node.name)}
        </span>
        <span className="import-tree-name">{node.name}</span>
        {node.type === "file" && node.size != null && (
          <span className="import-tree-size">{formatSize(node.size)}</span>
        )}
      </div>
      {expanded && node.children && (
        <div className="import-tree-children">
          {node.children.map((child) => (
            <FileTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              checkedPaths={checkedPaths}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

import { useState } from "react";

function _collectPaths(node: ImportTreeNode): string[] {
  const paths = [node.path];
  if (node.children) {
    for (const c of node.children) {
      paths.push(..._collectPaths(c));
    }
  }
  return paths;
}

function _fileIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["md", "mdx", "markdown"].includes(ext)) return "📝";
  if (ext === "csv") return "📊";
  if (ext === "json") return "📋";
  if (["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)) return "🖼️";
  if (["mp3", "wav", "ogg", "flac"].includes(ext)) return "🎵";
  if (["mp4", "webm", "mov"].includes(ext)) return "🎬";
  if (ext === "pdf") return "📄";
  if (["zip", "tar", "gz"].includes(ext)) return "📦";
  return "📄";
}
