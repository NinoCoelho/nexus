/**
 * Nexus settings tab — first tab in the SettingsDrawer.
 *
 * Renders a native account panel sourced from the backend's cached
 * ``/auth/nexus/status``: email, plan badge, connection state, and
 * the live spend gauge from the website's ``/api/status``. No iframe —
 * the desktop owns the rendering so the visuals stay consistent with
 * the rest of the app and we don't depend on the website being
 * iframe-embeddable.
 *
 * Connect button: re-opens the Firebase popup. The website's
 * ``connected`` flag in Firestore can only flip to true via a fresh
 * idToken (POST /api/keys/confirm), so the only way to recover from
 * a "Not connected" state is to re-sign-in.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation, Trans } from "react-i18next";
import { getConfig } from "../../api";
import { useNexusAccount } from "../../hooks/useNexusAccount";
import { useToast } from "../../toast/ToastProvider";
import Modal from "../Modal";
import NexusSignin from "../ProviderWizard/steps/NexusSignin";
import "./NexusTab.css";

function formatMoney(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value >= 100) return `$${value.toFixed(0)}`;
  return `$${value.toFixed(2)}`;
}

function formatResetIn(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  let secs = Math.max(0, Math.round((t - Date.now()) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return `${days}d`;
}

function spendBucket(ratio: number): "ok" | "warn" | "high" {
  if (ratio >= 0.9) return "high";
  if (ratio >= 0.7) return "warn";
  return "ok";
}

export default function NexusTab() {
  const { t } = useTranslation("settings");
  const toast = useToast();
  const account = useNexusAccount();
  const [websiteUrl, setWebsiteUrl] = useState<string>("https://www.nexus-model.us");
  const [showSignOutConfirm, setShowSignOutConfirm] = useState(false);
  const [showSignin, setShowSignin] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        if (cfg.nexus_account?.base_url) setWebsiteUrl(cfg.nexus_account.base_url);
      })
      .catch(() => {
        // /config errors are non-fatal — fall back to the default URL.
      });
  }, []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await account.refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "refresh failed");
    } finally {
      setRefreshing(false);
    }
  }, [account, toast]);

  const onConfirmSignOut = useCallback(async () => {
    setShowSignOutConfirm(false);
    try {
      await account.logout();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "logout failed");
    }
  }, [account, toast]);

  // Open the website's account/upgrade page in a focused popup. Stripe
  // Checkout / Billing Portal both set X-Frame-Options=DENY, so iframe
  // embedding is a non-starter for the actual payment step. The popup
  // keeps the user in-window enough that we can poll for completion:
  // when the popup closes, force a /api/status refresh so the tier
  // badge + gauge reflect a freshly-completed Stripe checkout without
  // waiting for the 5-minute watcher tick.
  const openUpgradePopup = useCallback(() => {
    const url = `${websiteUrl.replace(/\/$/, "")}/account`;
    let popup: Window | null = null;
    try {
      popup = window.open(url, "nexus-upgrade", "popup,width=520,height=760");
    } catch {
      popup = null;
    }
    if (!popup) {
      // Popup blocker engaged — fall back to a new tab rather than
      // failing silently. The user still gets to the page.
      window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    try { popup.focus(); } catch { /* same-origin oddity */ }
    const interval = window.setInterval(() => {
      if (popup!.closed) {
        window.clearInterval(interval);
        void account.refresh();
      }
    }, 500);
  }, [account, websiteUrl]);

  // Sub-view: in-tab sign-in step. Shown when the user clicks Connect
  // (re-establish the website's connected flag) or "Sign in again"
  // after a sign-out.
  if (showSignin) {
    return (
      <NexusSignin
        websiteUrl={websiteUrl}
        onSignedIn={(_payload) => {
          setShowSignin(false);
          void account.reload();
          toast.success(t("settings:nexus.account.connected"));
        }}
        onCancel={() => setShowSignin(false)}
        busy={false}
      />
    );
  }


  const status = account.status;
  const signedIn = status?.signedIn ?? false;
  const tier = status?.tier ?? "free";
  const cancelsAt = status?.cancelsAt ?? null;
  const trialEnd = status?.trialEnd ?? null;
  const tierLabel =
    tier === "pro"
      ? t("settings:nexus.account.tierPro")
      : t("settings:nexus.account.tierFree");
  const live = status?.status;
  const ratio =
    live && live.maxBudget > 0
      ? Math.min(1, Math.max(0, live.spend / live.maxBudget))
      : 0;
  const bucket = spendBucket(ratio);
  const resetIn = formatResetIn(live?.budgetResetAt);

  if (!signedIn) {
    // Out-of-band sign-out (e.g. user pressed Sign out elsewhere) —
    // show a single primary CTA instead of the panel.
    return (
      <div className="nexus-tab nexus-tab--empty">
        <button
          type="button"
          className="nexus-tab-signin"
          onClick={() => setShowSignin(true)}
        >
          {t("settings:nexus.account.signInAgain")}
        </button>
      </div>
    );
  }

  return (
    <div className="nexus-tab">
      <div className="nexus-tab-strip">
        <div className="nexus-tab-account">
          <Trans
            i18nKey="settings:nexus.account.signedInAs"
            values={{ email: status?.email || "" }}
            components={{ bold: <strong /> }}
          />
          <span className={`nexus-tab-tier nexus-tab-tier-${tier}`}>{tierLabel}</span>
        </div>
        <div className="nexus-tab-actions">
          <button type="button" disabled={refreshing} onClick={onRefresh}>
            {t("settings:nexus.account.refresh")}
          </button>
          <button type="button" onClick={() => setShowSignOutConfirm(true)}>
            {t("settings:nexus.account.signOut")}
          </button>
        </div>
      </div>

      <div className="nexus-tab-panel">
        <div className="nexus-tab-panel-header">
          <h3>{t("settings:nexus.account.panelTitle")}</h3>
        </div>

        <div className="nexus-tab-row">
          <div className="nexus-tab-row-label">{t("settings:nexus.account.emailLabel")}</div>
          <div className="nexus-tab-row-value">{status?.email || "—"}</div>
        </div>

        <div className="nexus-tab-row">
          <div className="nexus-tab-row-label">{t("settings:nexus.account.planLabel")}</div>
          <div className="nexus-tab-row-value">
            <div className="nexus-tab-plan-row">
              <span className={`nexus-tab-tier nexus-tab-tier-${tier}`}>{tierLabel}</span>
              {tier === "pro" && cancelsAt && (() => {
                const days = Math.max(0, Math.ceil((new Date(cancelsAt).getTime() - Date.now()) / 86400000));
                return (
                  <span className="nexus-tab-cancels">
                    {days > 0 ? `${days} day${days !== 1 ? "s" : ""} left` : "ends today"}
                    {" — "}{new Date(cancelsAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                  </span>
                );
              })()}
              {tier === "pro" && !cancelsAt && trialEnd && (() => {
                const days = Math.max(0, Math.ceil((new Date(trialEnd).getTime() - Date.now()) / 86400000));
                return (
                  <span className="nexus-tab-cancels">
                    {days > 0 ? `${days} day${days !== 1 ? "s" : ""} left on trial` : "trial ending soon"}
                  </span>
                );
              })()}
            </div>
            {tier === "pro" ? (
              <button
                type="button"
                className="nexus-tab-manage-btn"
                onClick={() => {
                  const url = `${websiteUrl.replace(/\/$/, "")}/billing`;
                  const popup = window.open(url, "nexus-billing", "popup,width=520,height=760");
                  if (!popup) {
                    window.open(url, "_blank", "noopener,noreferrer");
                    return;
                  }
                  try { popup.focus(); } catch { /* */ }
                  const interval = window.setInterval(() => {
                    if (popup.closed) {
                      window.clearInterval(interval);
                      void account.refresh();
                    }
                  }, 500);
                }}
              >
                {cancelsAt ? "Reactivate or manage subscription" : "Manage subscription"}
              </button>
            ) : (
              <button
                type="button"
                className="nexus-tab-link"
                onClick={openUpgradePopup}
              >
                {t("settings:nexus.account.upgrade")}
              </button>
            )}
          </div>
        </div>

        <div className="nexus-tab-row">
          <div className="nexus-tab-row-label">{t("settings:nexus.account.connectionLabel")}</div>
          <div className="nexus-tab-row-value nexus-tab-row-value--inline">
            <span
              className={
                status?.connected
                  ? "nexus-tab-conn nexus-tab-conn--ok"
                  : "nexus-tab-conn nexus-tab-conn--bad"
              }
            >
              {status?.connected
                ? t("settings:nexus.account.connected")
                : t("settings:nexus.account.notConnected")}
            </span>
            {!status?.connected && (
              <button
                type="button"
                className="nexus-tab-link"
                onClick={() => setShowSignin(true)}
              >
                {t("settings:nexus.account.connect")}
              </button>
            )}
          </div>
        </div>

        <div className="nexus-tab-row nexus-tab-row--usage">
          <div className="nexus-tab-row-label">{t("settings:nexus.account.usageLabel")}</div>
          <div className="nexus-tab-row-value nexus-tab-row-value--block">
            {live && live.maxBudget > 0 ? (
              <>
                <div className={`nexus-tab-bar nexus-tab-bar-${bucket}`}>
                  <div
                    className="nexus-tab-bar-fill"
                    style={{ width: `${Math.round(ratio * 100)}%` }}
                  />
                </div>
                <div className="nexus-tab-usage-meta">
                  <span className="nexus-tab-usage-spend">
                    {t("settings:nexus.account.usageOf", {
                      spend: formatMoney(live.spend),
                      max: formatMoney(live.maxBudget),
                    })}
                  </span>
                  {resetIn && (
                    <span className="nexus-tab-usage-period">
                      {t("settings:nexus.account.usagePeriod", {
                        period: live.budgetDuration || "",
                        time: resetIn,
                      })}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <span className="nexus-tab-usage-empty">
                {t("settings:nexus.account.usageNoData")}
              </span>
            )}
          </div>
        </div>
      </div>

      {showSignOutConfirm && (
        <Modal
          kind="confirm"
          title={t("settings:nexus.account.signOut")}
          message={t("settings:nexus.account.signOutConfirm")}
          confirmLabel={t("settings:nexus.account.signOut")}
          danger
          onCancel={() => setShowSignOutConfirm(false)}
          onSubmit={onConfirmSignOut}
        />
      )}
    </div>
  );
}
