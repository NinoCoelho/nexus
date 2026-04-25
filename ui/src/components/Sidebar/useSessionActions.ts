// Session CRUD actions extracted from Sidebar/index.tsx.

import React from "react";
import {
  deleteSession,
  exportSession,
  importSession,
  patchSession,
  sessionToVault,
  type SessionSummary,
} from "../../api";
interface ToastAPI {
  success: (msg: string, opts?: { detail?: string; duration?: number }) => void;
  error: (msg: string, opts?: { detail?: string }) => void;
  info: (msg: string, opts?: { duration?: number }) => void;
}

interface SessionActionsOptions {
  sessions: SessionSummary[];
  setSessions: React.Dispatch<React.SetStateAction<SessionSummary[]>>;
  renamingId: string | null;
  renameValue: string;
  setRenamingId: (id: string | null) => void;
  setMenuNull: () => void;
  setToVaultBusy: React.Dispatch<React.SetStateAction<Set<string>>>;
  onSessionsRevisionBump: () => void;
  onSessionSelect: (id: string) => void;
  toast: ToastAPI;
}

export function useSessionActions({
  sessions,
  setSessions,
  renamingId,
  renameValue,
  setRenamingId,
  setMenuNull,
  setToVaultBusy,
  onSessionsRevisionBump,
  onSessionSelect,
  toast,
}: SessionActionsOptions) {
  const handleRename = async (id: string) => {
    try {
      await patchSession(id, { title: renameValue.trim() || "Untitled" });
      setSessions((prev) =>
        prev.map((s) => s.id === id ? { ...s, title: renameValue.trim() || "Untitled" } : s)
      );
    } catch { /* ignore */ }
    setRenamingId(null);
    setMenuNull();
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch { /* ignore */ }
    setMenuNull();
  };

  const handleExport = async (id: string) => {
    setMenuNull();
    try {
      const { markdown, filename } = await exportSession(id);
      const blob = new Blob([markdown], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
      toast.success(`Downloaded ${filename}`);
    } catch (e) {
      toast.error("Download failed", { detail: e instanceof Error ? e.message : undefined });
    }
  };

  const handleToVault = async (id: string, mode: "raw" | "summary") => {
    setMenuNull();
    setToVaultBusy((prev) => new Set(prev).add(id));
    if (mode === "summary") toast.info("Summarizing session…", { duration: 2500 });
    try {
      const result = await sessionToVault(id, mode);
      toast.success(
        mode === "raw" ? "Saved raw to vault" : "Summary saved to vault",
        { detail: result.path, duration: 5000 },
      );
    } catch (e) {
      toast.error(
        mode === "raw" ? "Couldn't save raw to vault" : "Summarize failed",
        { detail: e instanceof Error ? e.message : undefined },
      );
    } finally {
      setToVaultBusy((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Reset so the same file can be picked again.
    e.target.value = "";
    try {
      const text = await file.text();
      const result = await importSession(text);
      onSessionsRevisionBump();
      onSessionSelect(result.id);
    } catch { /* ignore */ }
  };

  void sessions; // referenced via closure in rename/delete
  void renamingId;

  return { handleRename, handleDelete, handleExport, handleToVault, handleImportFile };
}
