// Custom hook for VaultTreePanel: file/folder CRUD and dispatch actions.

import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { ModalProps } from "../Modal";
import {
  createVaultKanban,
  deleteVaultFile,
  dispatchFromVault,
  postVaultFolder,
  postVaultMove,
  putVaultFile,
  undoVaultPath,
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
  const { t } = useTranslation("vault");
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
      toast.error(t("vault:toast.moveFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  }, [selectedPath, onSelectPath, refreshTree, onTreeChange, toast]);

  const handleRename = useCallback((node: TreeNode) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: t("vault:modal.renameTitle"),
      defaultValue: node.name,
      confirmLabel: t("vault:modal.renameLabel"),
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
          toast.error(t("vault:toast.renameFailed"), { detail: e instanceof Error ? e.message : undefined });
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
      title: t("vault:modal.newFileTitle"),
      message: dirPath ? t("vault:modal.newFileCreatingIn", { dir: dirPath }) : undefined,
      defaultValue: t("vault:modal.newFileDefault"),
      confirmLabel: t("vault:modal.newFileCreate"),
      onCancel: () => setModal(null),
      onSubmit: async (name) => {
        setModal(null);
        const path = dirPath ? `${dirPath}/${name}` : name;
        try {
          await putVaultFile(path, "");
          refreshTree();
          onSelectPath(path);
        } catch (e) {
          toast.error(t("vault:toast.createFileFailed"), { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  }, [refreshTree, onSelectPath, toast, setModal, setCtxMenu]);

  const handleNewFolder = useCallback((parentPath?: string) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: t("vault:modal.newFolderTitle"),
      message: parentPath ? t("vault:modal.newFolderCreatingIn", { dir: parentPath }) : undefined,
      placeholder: t("vault:modal.newFolderPlaceholder"),
      confirmLabel: t("vault:modal.newFolderCreate"),
      onCancel: () => setModal(null),
      onSubmit: async (name) => {
        setModal(null);
        const path = parentPath ? `${parentPath}/${name}` : name;
        try {
          await postVaultFolder(path);
          refreshTree();
        } catch (e) {
          toast.error(t("vault:toast.createFolderFailed"), { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  }, [refreshTree, toast, setModal, setCtxMenu]);

  const handleNewKanban = useCallback((dirPath?: string) => {
    setCtxMenu(null);
    setModal({
      kind: "prompt",
      title: t("vault:modal.newKanbanTitle"),
      message: dirPath ? t("vault:modal.newKanbanCreatingIn", { dir: dirPath }) : undefined,
      defaultValue: t("vault:modal.newKanbanDefault"),
      confirmLabel: t("vault:modal.newKanbanCreate"),
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
          toast.error(t("vault:toast.createKanbanFailed"), { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  }, [refreshTree, onSelectPath, toast, setModal, setCtxMenu]);

  const handleDispatchFile = useCallback(async (filePath: string) => {
    try {
      const res = await dispatchFromVault({ path: filePath });
      onDispatchToChat?.(res.session_id, res.seed_message ?? "");
    } catch (e) {
      toast.error(t("vault:toast.chatStartFailed"), { detail: e instanceof Error ? e.message : undefined });
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
      toast.error(t("vault:toast.deleteFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  }, [selectedPath, onSelectPath, refreshTree, onTreeChange, toast, t]);

  const handleDelete = useCallback((node: TreeNode) => {
    setCtxMenu(null);
    if (node.type === "file") {
      setModal({
        kind: "confirm",
        title: t("vault:modal.deleteFileTitle"),
        message: t("vault:modal.deleteFileMessage", { name: node.name }),
        confirmLabel: t("vault:modal.deleteFileCta"),
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
        title: t("vault:modal.deleteFolderTitle"),
        message: t("vault:modal.deleteFolderEmptyMessage", { name: node.name }),
        confirmLabel: t("vault:modal.deleteFolderCta"),
        danger: true,
        onCancel: () => setModal(null),
        onSubmit: () => { setModal(null); void doDelete(node.path, false); },
      });
      return;
    }
    // Non-empty: first confirm, then second confirmation.
    const summary = [
      counts.files > 0 && t("vault:modal.deleteFolderFiles", { count: counts.files }),
      counts.dirs > 0 && t("vault:modal.deleteFolderSubfolders", { count: counts.dirs }),
    ].filter(Boolean).join(", ");
    setModal({
      kind: "confirm",
      title: t("vault:modal.deleteFolderNonEmptyTitle"),
      message: t("vault:modal.deleteFolderNonEmptyMessage", { name: node.name, summary }),
      confirmLabel: t("vault:modal.deleteFolderContinue"),
      danger: true,
      onCancel: () => setModal(null),
      onSubmit: () => {
        setModal({
          kind: "prompt",
          title: t("vault:modal.deleteFolderConfirmTitle"),
          message: t("vault:modal.deleteFolderConfirmMessage", { name: node.name, summary }),
          placeholder: node.name,
          confirmLabel: t("vault:modal.deleteFolderForever"),
          onCancel: () => setModal(null),
          onSubmit: (typed) => {
            if (typed.trim() !== node.name) {
              toast.error(t("vault:toast.deleteFailed"));
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

  const handleUndo = useCallback(async (node: TreeNode) => {
    setCtxMenu(null);
    setModal({
      kind: "confirm",
      title: t("vault:contextMenu.undoLastChange"),
      message: `Step "${node.path}" back one revision in history?`,
      confirmLabel: t("vault:contextMenu.undoLastChange"),
      onCancel: () => setModal(null),
      onSubmit: async () => {
        setModal(null);
        try {
          const r = await undoVaultPath(node.path);
          if (!r.undone) {
            toast.error(
              r.reason === "no_history"
                ? t("vault:history.nothingToUndo")
                : t("vault:history.undoFailed", { reason: r.reason ?? "unknown" }),
            );
            return;
          }
          refreshTree();
          onTreeChange?.();
          toast.success(t("vault:toast.undoDone", { count: r.paths.length }));
        } catch (e) {
          toast.error(t("vault:toast.undoFailed"), { detail: e instanceof Error ? e.message : undefined });
        }
      },
    });
  }, [refreshTree, onTreeChange, toast, setModal, setCtxMenu]);

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
      toast.success(t("vault:toast.uploaded", { count: result.uploaded.length }));
      refreshTree();
      if (result.uploaded.length === 1) onSelectPath(result.uploaded[0].path);
    } catch (err) {
      toast.error(t("vault:toast.uploadFailed"), { detail: err instanceof Error ? err.message : undefined });
    }
    e.target.value = "";
  }, [selectedPath, rawNodes, refreshTree, onSelectPath, toast, t]);

  return {
    handleMove,
    handleRename,
    handleCtxUpload,
    handleNewFile,
    handleNewFolder,
    handleNewKanban,
    handleDispatchFile,
    handleDelete,
    handleUndo,
    handleUpload,
  };
}
