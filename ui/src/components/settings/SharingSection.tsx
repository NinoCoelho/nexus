import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getTunnelStatus,
  startTunnel,
  stopTunnel,
  type TunnelStatus,
} from "../../api/tunnel";
import { useToast } from "../../toast/ToastProvider";
import { useAuthState } from "../AuthGate";
import Modal from "../Modal";
import SettingsSection from "./SettingsSection";

export default function SharingSection() {
  const { t } = useTranslation("tunnel");
  const { proxied } = useAuthState();
  const toast = useToast();
  const [status, setStatus] = useState<TunnelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qrCanvasRef = useRef<HTMLCanvasElement>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await getTunnelStatus();
      setStatus(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("tunnel:sharing.toast.stopFailed"));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // While a tunnel is up but nobody's redeemed the code yet, poll status so
  // the panel can flip to the "code burned" state within a few seconds of the
  // pairing happening on the phone. We stop polling once redemption flips
  // (steady state) or the tunnel goes inactive.
  useEffect(() => {
    if (!status?.active || status?.redeemed) return;
    const id = window.setInterval(refresh, 4000);
    return () => window.clearInterval(id);
  }, [status?.active, status?.redeemed, refresh]);

  // Render the QR code into a canvas whenever the share URL changes. The QR
  // encodes the *plain* URL only — never the access code. The phone scans the
  // URL, opens it, and sees the login form; the user types the code there.
  useEffect(() => {
    const url = status?.share_url;
    const canvas = qrCanvasRef.current;
    if (!url || !canvas) return;
    let cancelled = false;
    (async () => {
      try {
        const QR = (await import("qrcode")).default;
        if (cancelled) return;
        await QR.toCanvas(canvas, url, { width: 200, margin: 1 });
      } catch (e) {
        // qrcode is optional UX; failure shouldn't block the URL display.
        console.warn("QR render failed", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [status?.share_url]);

  const handleActivate = async () => {
    setConfirmOpen(false);
    setBusy(true);
    setError(null);

    // First-time activation downloads cloudflared (~30 MB) before the tunnel
    // comes up. Show a long-running progress toast so the user knows what's
    // happening and isn't tempted to retry.
    const firstRun = status?.binary_installed === false;
    const tid = toast.info(
      firstRun ? t("tunnel:sharing.toast.preparingFirst") : t("tunnel:sharing.toast.preparing"),
      {
        detail: firstRun
          ? t("tunnel:sharing.toast.preparingFirstDetail")
          : t("tunnel:sharing.toast.preparingDetail"),
        duration: 180_000,
      },
    );

    try {
      const s = await startTunnel();
      setStatus(s);
      toast.dismiss(tid);
      toast.success(t("tunnel:sharing.toast.live"));
    } catch (e) {
      const msg = e instanceof Error ? e.message : t("tunnel:sharing.toast.stopFailed");
      setError(msg);
      toast.dismiss(tid);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      const s = await stopTunnel();
      setStatus(s);
      toast.info(t("tunnel:sharing.toast.revoked"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("tunnel:sharing.toast.stopFailed"));
    } finally {
      setBusy(false);
    }
  };

  const copy = async (text: string, kind: "url" | "code") => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(kind === "url" ? t("tunnel:sharing.toast.urlCopied") : t("tunnel:sharing.toast.codeCopied"));
    } catch {
      toast.error(t("tunnel:sharing.toast.copyFailed"));
    }
  };

  const active = status?.active === true;

  // Sharing is administered only by the loopback owner. A redeemer reaching
  // this UI through the tunnel must not see (or attempt to call) start/stop —
  // those routes 403 anyway, but the panel itself would be confusing.
  if (proxied) return null;

  return (
    <>
      <SettingsSection
        title={t("tunnel:sharing.sectionTitle")}
        icon="🔗"
        collapsible
        defaultOpen={false}
        help={{
          title: t("tunnel:sharing.helpTitle"),
          body: (
            <>
              Opens a public Cloudflare Quick Tunnel to this Nexus and gives
              you a URL plus a short access code. Open the URL on your phone
              (or share it), type the code on the phone's login screen, and
              the same UI as your desktop loads. The code is burned the moment
              a device redeems it; to let another device in, stop and restart
              sharing for a fresh code. Stop sharing to revoke instantly. The
              code is the credential — keep it private; the URL alone is
              harmless.
            </>
          ),
        }}
      >
        {error && <p className="settings-error">{error}</p>}

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {!active && (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontSize: 12, opacity: 0.75, lineHeight: 1.5 }}>
                {t("tunnel:sharing.inactive.description")}
                {status?.binary_installed === false && (
                  <>
                    {" "}
                    <strong>{t("tunnel:sharing.inactive.firstTimeNote")}</strong>
                  </>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                  className="settings-btn settings-btn--primary"
                  onClick={() => setConfirmOpen(true)}
                  disabled={busy}
                >
                  {busy ? t("tunnel:sharing.inactive.preparingButton") : t("tunnel:sharing.inactive.activateButton")}
                </button>
              </div>
            </div>
          )}

          {active && status && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 14,
                padding: 14,
                border: "1px solid var(--border, #333)",
                borderRadius: 8,
              }}
            >
              <div
                role="alert"
                style={{
                  padding: "10px 12px",
                  borderRadius: 6,
                  background: "rgba(255, 170, 60, 0.08)",
                  border: "1px solid rgba(255, 170, 60, 0.35)",
                  fontSize: 12,
                  lineHeight: 1.5,
                }}
              >
                <strong style={{ color: "#ffb84d" }}>⚠ {t("tunnel:sharing.active.warning")}</strong>{" "}
                {t("tunnel:sharing.active.warningBody")}
              </div>
              <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                <canvas
                  ref={qrCanvasRef}
                  style={{
                    background: "#fff",
                    borderRadius: 4,
                    flexShrink: 0,
                  }}
                />
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 10,
                    minWidth: 0,
                    flex: 1,
                  }}
                >
                  <div>
                    <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>
                      {t("tunnel:sharing.active.openUrl")}
                    </div>
                    <code
                      style={{
                        display: "block",
                        fontSize: 11,
                        wordBreak: "break-all",
                        userSelect: "all",
                        padding: "5px 7px",
                        background: "var(--code-bg, #222)",
                        borderRadius: 4,
                      }}
                    >
                      {status.share_url}
                    </code>
                  </div>

                  {status.code ? (
                    <div>
                      <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>
                        {t("tunnel:sharing.active.typeCode")}
                      </div>
                      <div
                        style={{
                          fontFamily: "ui-monospace, monospace",
                          fontSize: 22,
                          letterSpacing: 4,
                          padding: "8px 12px",
                          background: "var(--code-bg, #222)",
                          borderRadius: 6,
                          textAlign: "center",
                          userSelect: "all",
                        }}
                      >
                        {status.code}
                      </div>
                    </div>
                  ) : status.redeemed ? (
                    <div
                      style={{
                        fontSize: 12,
                        lineHeight: 1.5,
                        padding: "8px 12px",
                        borderRadius: 6,
                        background: "rgba(80, 200, 120, 0.08)",
                        border: "1px solid rgba(80, 200, 120, 0.35)",
                      }}
                    >
                      <strong style={{ color: "#5fd38a" }}>✓ {t("tunnel:sharing.active.paired")}</strong>{" "}
                      {t("tunnel:sharing.active.pairedBody")}
                    </div>
                  ) : null}

                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button
                      className="settings-btn"
                      onClick={() => status.share_url && copy(status.share_url, "url")}
                      disabled={busy}
                    >
                      {t("tunnel:sharing.active.copyUrl")}
                    </button>
                    {status.code && (
                      <button
                        className="settings-btn"
                        onClick={() => copy(status.code!, "code")}
                        disabled={busy}
                      >
                        {t("tunnel:sharing.active.copyCode")}
                      </button>
                    )}
                    <button
                      className="settings-btn settings-btn--danger"
                      onClick={handleStop}
                      disabled={busy}
                    >
                      {t("tunnel:sharing.active.stopSharing")}
                    </button>
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 11, opacity: 0.6 }}>
                {t("tunnel:sharing.active.urlSafeNote")}
              </div>
            </div>
          )}
        </div>
      </SettingsSection>

      {confirmOpen && (
        <Modal
          kind="confirm"
          title={t("tunnel:sharing.confirm.title")}
          message={t("tunnel:sharing.confirm.message")}
          confirmLabel={t("tunnel:sharing.confirm.confirmLabel")}
          danger
          onCancel={() => setConfirmOpen(false)}
          onSubmit={handleActivate}
        />
      )}
    </>
  );
}
