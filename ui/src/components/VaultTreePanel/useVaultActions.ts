// Custom hook for VaultTreePanel: file/folder CRUD and dispatch actions.

import { useCallback } from "react";
import type { ModalProps } from "../Modal";
import {
  createVaultKanban,
  deleteVaultFile,
  dispatchFromVault,
  postVaultFolder,
  postVaultMove,
  putVaultFile,
  uploadVaultFiles,
} from "../../api";
import type { TreeNode } from "./types";

interface UseVaultActionsOptions {
  selectedPath: string | null;
  rawNodes: import("../../api").VaultNode[];
  refreshTree: () => void;
  onSelectPath: (path: string | null) => void;
  onTreeChange?: () => void;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  toast: { success: (msg: string) => void; error: (msg: string, opts?: { detail?: string }) => void };
  setModal: (m: ModalProps | null) => void;
  setCtxMenu: (m: null) => void;
  descendantCounts: Map<string, { files: number; dirs: number }>;
  uploadCtxDirRef: React.RefObject<HTMLInputElement | null>;
}

export function useVaultActions({
  selectedPath,
  rawNodes,
  refreshTree,
  onSelectPath,
  onTreeChange,
  onDispatchToChat,
  toast,
  setModal,
  setCtxMenu,
  descendantCounts,
  uploadCtxDirRef,
}: UseVaultActionsOptions) {
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
  }, [selectedPath, onSelectPath, refreshTree, onTreeChange, toast, setModal, setCtxMenu]);

  const handleCtxUpload = useCallback((dirPath: string) => {
    setCtxMenu(null);
    uploadCtxDirRef.current?.setAttribute("data-dest-dir", dirPath);
    uploadCtxDirRef.current?.click();
  }, [setCtxMenu, uploadCtxDirRef]);

  const handleNewFile = useCallback((dirPath?: string) => {
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
  }, [refreshTree, onSelectPath, toast, setModal, setCtxMenu]);

  const handleNewFolder = useCallback((parentPath?: string) => {
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
  }, [refreshTree, toast, setModal, setCtxMenu]);

  const handleNewKanban = useCallback((dirPath?: string) => {
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
  }, [refreshTree, onSelectPath, toast, setModal, setCtxMenu]);

  const handleDispatchFile = useCallback(async (filePath: string) => {
    try {
      const res = await dispatchFromVault({ path: filePath });
      onDispatchToChat?.(res.session_id, res.seed_message ?? "");
    } catch (e) {
      toast.error("Couldn't start chat", { detail: e instanceof Error ? e.message : undefined });
    }
    setCtxMenu(null);
  }, [onDispatchToChat, toast, setCtxMenu]);

  const doDelete = useCallback(async (path: string, recursive: boolean) => {
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
  }, [selectedPath, onSelectPath, refreshTree, onTreeChange, toast]);

  const handleDelete = useCallback((node: TreeNode) => {
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
  }, [descendantCounts, toast, setModal, setCtxMenu, doDelete]);

  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>, overrideDir?: string) => {
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
  }, [selectedPath, rawNodes, refreshTree, onSelectPath, toast]);

  return {
    handleMove,
    handleRename,
    handleCtxUpload,
    handleNewFile,
    handleNewFolder,
    handleNewKanban,
    handleDispatchFile,
    handleDelete,
    handleUpload,
  };
}
