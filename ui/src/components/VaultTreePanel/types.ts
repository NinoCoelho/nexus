// Shared types for VaultTreePanel sub-components.

export interface TreeNode {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
  mtime?: number;
  children?: TreeNode[];
}
