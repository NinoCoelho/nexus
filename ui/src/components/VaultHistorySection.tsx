/**
 * VaultHistorySection — settings UI for the opt-in vault history.
 *
 * Shows: the on/off toggle, current commit count, last activity timestamp,
 * and a "Purge" action that runs ``git gc`` to reclaim space.
 */

import { useCallback, useEffect, useState } from "react";
import {
  disableVaultHistory,
  enableVaultHistory,
  getVaultHistoryStatus,
  purgeVaultHistory,
  type VaultHistoryStatus,
} from "../api";
import { useToast } from "../toast/ToastProvider";

function formatRelative(unixSec: number): string {
  const delta = Math.max(0, Date.now() / 1000 - unixSec);
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export default function VaultHistorySection() {
  const toast = useToast();
  const [status, setStatus] = useState<VaultHistoryStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getVaultHistoryStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const toggle = useCallback(async () => {
    if (!status || busy) return;
    setBusy(true);
    try {
      const next = status.enabled ? await disableVaultHistory() : await enableVaultHistory();
      setStatus(next);
      toast.success(next.enabled ? "History enabled" : "History disabled");
    } catch (e) {
      toast.error(
        status.enabled ? "Couldn't disable history" : "Couldn't enable history",
        { detail: e instanceof Error ? e.message : undefined },
      );
    } finally {
      setBusy(false);
    }
  }, [status, busy, toast]);

  const purge = useCallback(async () => {
    if (!status || busy) return;
    setBusy(true);
    try {
      const r = await purgeVaultHistory();
      if (r.ok) {
        toast.success("History purged");
        await refresh();
      } else {
        toast.error(`Purge failed: ${r.reason ?? "unknown"}`);
      }
    } catch (e) {
      toast.error("Purge failed", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }, [status, busy, toast, refresh]);

  if (!status) return <p className="settings-info">Loading…</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="hitl-row">
        <div className="hitl-row-text">
          <span className="hitl-row-label">Enabled</span>
          <p className="hitl-row-desc">
            When on, every vault save commits to a private git work-tree at{" "}
            <code>~/.nexus/.vault-history</code>. Right-click a file or folder
            to undo the most recent change.
          </p>
        </div>
        <button
          className={`hitl-switch${status.enabled ? " on" : ""}`}
          onClick={() => void toggle()}
          disabled={busy || !status.git_available}
          aria-pressed={status.enabled}
          title={!status.git_available ? "git is not on PATH" : undefined}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>
      {!status.git_available && (
        <p className="settings-error">
          <code>git</code> is not on PATH — install it to enable history.
        </p>
      )}
      {status.enabled && (
        <>
          <div className="settings-row">
            <span className="settings-row-name">{status.commit_count} commits</span>
            <span style={{ color: "var(--fg-faint)", fontSize: 12 }}>
              {status.last_commit
                ? `last: ${status.last_commit.action} · ${formatRelative(status.last_commit.timestamp)}`
                : "no activity yet"}
            </span>
          </div>
          <button
            className="settings-btn"
            style={{ alignSelf: "flex-start" }}
            onClick={() => void purge()}
            disabled={busy}
            title="Run git gc to reclaim storage space (does not change history visibility)"
          >
            Purge unreachable objects
          </button>
        </>
      )}
    </div>
  );
}
