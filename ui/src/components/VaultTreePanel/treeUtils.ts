// Pure tree-building helpers for VaultTreePanel.

import type { VaultNode } from "../../api";
import type { TreeNode } from "./types";

export function buildTree(nodes: VaultNode[]): TreeNode[] {
  const root: TreeNode[] = [];
  const map = new Map<string, TreeNode>();

  const sorted = [...nodes].sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    return a.path.localeCompare(b.path);
  });

  for (const n of sorted) {
    const parts = n.path.split("/");
    const name = parts[parts.length - 1];
    const node: TreeNode = {
      name,
      path: n.path,
      type: n.type,
      size: n.size,
      mtime: n.mtime,
      children: n.type === "dir" ? [] : undefined,
    };
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

/** Compute per-directory descendant counts for tooltip display. */
export function buildDescendantCounts(rawNodes: VaultNode[]): Map<string, { files: number; dirs: number }> {
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
}
