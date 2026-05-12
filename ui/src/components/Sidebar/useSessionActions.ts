// Session CRUD actions extracted from Sidebar/index.tsx.

import React from "react";
import { useTranslation } from "react-i18next";
import {
  createSessionShare,
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
  info: (
    msg: string,
    opts?: {
      detail?: string;
      duration?: number;
      action?: { label: string; onClick: () => void; keepOpen?: boolean };
    },
  ) => void;
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
  /** Currently active session — used so deleting it can blank the canvas. */
  activeSessionId: string | null;
  /** Fired when the deleted session was the active one. The host clears the chat surface. */
  onActiveSessionDeleted: () => void;
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
  activeSessionId,
  onActiveSessionDeleted,
  toast,
}: SessionActionsOptions) {
  const { t } = useTranslation("sidebar");
  const handleRename = async (id: string) => {
    const newTitle = renameValue.trim() || "Untitled";
    setSessions((prev) =>
      prev.map((s) => s.id === id ? { ...s, title: newTitle } : s)
    );
    try {
      await patchSession(id, { title: newTitle });
      onSessionsRevisionBump();
    } catch { /* ignore */ }
    setRenamingId(null);
    setMenuNull();
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (id === activeSessionId) onActiveSessionDeleted();
      onSessionsRevisionBump();
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
      toast.success(t("sidebar:session.downloadedFile", { filename }));
    } catch (e) {
      toast.error(t("sidebar:session.downloadFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  };

  const handleToVault = async (id: string, mode: "raw" | "summary") => {
    setMenuNull();
    setToVaultBusy((prev) => new Set(prev).add(id));
    if (mode === "summary") toast.info(t("sidebar:session.summarizing"), { duration: 2500 });
    try {
      const result = await sessionToVault(id, mode);
      toast.success(
        mode === "raw" ? t("sidebar:session.savedRaw") : t("sidebar:session.summarySaved"),
        { detail: result.path, duration: 5000 },
      );
    } catch (e) {
      toast.error(
        mode === "raw" ? t("sidebar:session.saveRawFailed") : t("sidebar:session.summarizeFailed"),
        { detail: e instanceof Error ? e.message : undefined },
      );
    } finally {
      setToVaultBusy((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  };

  const handleShare = async (id: string) => {
    setMenuNull();
    try {
      const link = await createSessionShare(id);
      const fullUrl = `${window.location.origin}${window.location.pathname}${link.path}`;
      try {
        await navigator.clipboard.writeText(fullUrl);
        toast.success(t("sidebar:session.shareCopied"), { detail: t("sidebar:session.shareCopiedDetail"), duration: 5000 });
      } catch {
        toast.info(t("sidebar:session.shareReady"), {
          detail: fullUrl,
          duration: 12000,
          action: {
            label: t("sidebar:session.shareCopyLabel"),
            onClick: () => { void navigator.clipboard.writeText(fullUrl).catch(() => {}); },
          },
        });
      }
    } catch (e) {
      toast.error(t("sidebar:session.shareFailed"), { detail: e instanceof Error ? e.message : undefined });
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

  return { handleRename, handleDelete, handleExport, handleToVault, handleShare, handleImportFile };
}
