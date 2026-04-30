/**
 * VaultHistorySection — settings UI for the opt-in vault history.
 *
 * Shows: the on/off toggle, current commit count, last activity timestamp,
 * and a "Purge" action that runs ``git gc`` to reclaim space.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("vault");
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
      toast.success(next.enabled ? t("vault:historySection.toast.enabled") : t("vault:historySection.toast.disabled"));
    } catch (e) {
      toast.error(
        status.enabled ? t("vault:historySection.toast.disableFailed") : t("vault:historySection.toast.enableFailed"),
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
        toast.success(t("vault:historySection.toast.purged"));
        await refresh();
      } else {
        toast.error(t("vault:historySection.toast.purgeFailed", { reason: r.reason ?? "unknown" }));
      }
    } catch (e) {
      toast.error(t("vault:historySection.toast.purgeFailed", { reason: e instanceof Error ? e.message : "unknown" }));
    } finally {
      setBusy(false);
    }
  }, [status, busy, toast, refresh]);

  if (!status) return <p className="settings-info">{t("vault:history.loading")}</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="hitl-row">
        <div className="hitl-row-text">
          <span className="hitl-row-label">{t("vault:historySection.enabled")}</span>
          <p className="hitl-row-desc">{t("vault:historySection.enabledDescription")}</p>
        </div>
        <button
          className={`hitl-switch${status.enabled ? " on" : ""}`}
          onClick={() => void toggle()}
          disabled={busy || !status.git_available}
          aria-pressed={status.enabled}
          title={!status.git_available ? t("vault:historySection.gitNotOnPath") : undefined}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>
      {!status.git_available && (
        <p className="settings-error">
          {t("vault:historySection.gitNotAvailable")}
        </p>
      )}
      {status.enabled && (
        <>
          <div className="settings-row">
            <span className="settings-row-name">{t("vault:historySection.commits", { count: status.commit_count })}</span>
            <span style={{ color: "var(--fg-faint)", fontSize: 12 }}>
              {status.last_commit
                ? t("vault:historySection.lastActivity", { action: status.last_commit.action, when: formatRelative(status.last_commit.timestamp) })
                : t("vault:historySection.noActivity")}
            </span>
          </div>
          <button
            className="settings-btn"
            style={{ alignSelf: "flex-start" }}
            onClick={() => void purge()}
            disabled={busy}
            title={t("vault:historySection.purgeTitle")}
          >
            {t("vault:historySection.purge")}
          </button>
        </>
      )}
    </div>
  );
}
