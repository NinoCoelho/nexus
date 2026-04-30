/**
 * VaultHistoryPanel — slide-out drawer listing recent commits for a path.
 *
 * Shows the last N commits touching ``path`` from the opt-in vault history
 * (git-backed). The only mutating action is the "Undo" button, which steps
 * the path back one real commit. Commits with the ``undo:`` action are still
 * listed for transparency but are skipped by the undo walk.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getVaultHistory,
  getVaultHistoryStatus,
  undoVaultPath,
  type VaultHistoryCommit,
  type VaultHistoryStatus,
} from "../api";
import "./SkillDrawer.css";
import "./VaultHistoryPanel.css";

interface Props {
  path: string;
  onClose: () => void;
  /** Called after a successful undo so the host can reload buffers, etc. */
  onUndone?: (touched: string[]) => void;
}

function formatRelative(unixSec: number): string {
  const delta = Math.max(0, Date.now() / 1000 - unixSec);
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export default function VaultHistoryPanel({ path, onClose, onUndone }: Props) {
  const { t } = useTranslation("vault");
  const [status, setStatus] = useState<VaultHistoryStatus | null>(null);
  const [commits, setCommits] = useState<VaultHistoryCommit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, log] = await Promise.all([
        getVaultHistoryStatus(),
        getVaultHistory(path, 100),
      ]);
      setStatus(s);
      setCommits(log);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("vault:history.undoFailed", { reason: "unknown" }));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleUndo = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await undoVaultPath(path);
      if (!r.undone) {
        setError(
          r.reason === "no_history"
            ? t("vault:history.nothingToUndo")
            : t("vault:history.undoFailed", { reason: r.reason ?? "unknown" }),
        );
      } else {
        onUndone?.(r.paths);
        await refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("vault:history.undoFailed", { reason: "unknown" }));
    } finally {
      setBusy(false);
    }
  }, [busy, path, refresh, onUndone]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="vault-history-drawer">
        <div className="drawer-header">
          <span className="drawer-title">{t("vault:history.drawerTitle", { path })}</span>
          <button className="drawer-close" onClick={onClose} aria-label={t("vault:history.closeAria")}>
            ✕
          </button>
        </div>
        <div className="vault-history-toolbar">
          <button
            className="settings-btn settings-btn--primary"
            onClick={() => void handleUndo()}
            disabled={busy || !status?.enabled || commits.length === 0}
          >
            {busy ? t("vault:history.undoing") : t("vault:history.undoLastChange")}
          </button>
          {status && !status.enabled && (
            <span className="vault-history-hint">{t("vault:history.historyDisabled")}</span>
          )}
        </div>
        <div className="vault-history-body">
          {loading && <div className="vault-history-empty">{t("vault:history.loading")}</div>}
          {error && <div className="vault-history-error">{error}</div>}
          {!loading && !error && commits.length === 0 && (
            <div className="vault-history-empty">{t("vault:history.noHistory")}</div>
          )}
          {!loading && commits.length > 0 && (
            <ul className="vault-history-list">
              {commits.map((c) => (
                <li key={c.sha} className={`vault-history-row vault-history-row--${c.action}`}>
                  <div className="vault-history-row-main">
                    <span className={`vault-history-badge vault-history-badge--${c.action}`}>
                      {c.action}
                    </span>
                    <span className="vault-history-msg">{c.message}</span>
                  </div>
                  <span className="vault-history-when" title={new Date(c.timestamp * 1000).toLocaleString()}>
                    {formatRelative(c.timestamp)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}
