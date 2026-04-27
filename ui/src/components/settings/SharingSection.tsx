import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAuthtokenStatus,
  getTunnelStatus,
  installNgrokBinary,
  setAuthtoken,
  startTunnel,
  stopTunnel,
  type TunnelStatus,
} from "../../api/tunnel";
import { useToast } from "../../toast/ToastProvider";
import Modal from "../Modal";
import SettingsSection from "./SettingsSection";

export default function SharingSection() {
  const toast = useToast();
  const [status, setStatus] = useState<TunnelStatus | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qrCanvasRef = useRef<HTMLCanvasElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([getTunnelStatus(), getAuthtokenStatus()]);
      setStatus(s);
      setHasToken(a.configured);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load tunnel status");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

  const handleSaveToken = async () => {
    const value = tokenInput.trim();
    if (!value) return;
    setBusy(true);
    try {
      await setAuthtoken(value);
      setTokenInput("");
      setHasToken(true);
      toast.success("ngrok authtoken saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to save token");
    } finally {
      setBusy(false);
    }
  };

  const handleClearToken = async () => {
    setBusy(true);
    try {
      await setAuthtoken("");
      setHasToken(false);
      toast.info("ngrok authtoken cleared");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to clear token");
    } finally {
      setBusy(false);
    }
  };

  const handleInstall = async () => {
    setBusy(true);
    setError(null);
    const tid = toast.info("Downloading ngrok binary…", {
      detail: "This is a one-time ~10 MB download.",
      duration: 30000,
    });
    try {
      await installNgrokBinary();
      toast.update(tid, { message: "ngrok installed" });
      await refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "ngrok install failed";
      setError(msg);
      toast.update(tid, { message: msg });
    } finally {
      setBusy(false);
    }
  };

  const handleActivate = async () => {
    setConfirmOpen(false);
    setBusy(true);
    setError(null);
    try {
      const s = await startTunnel();
      setStatus(s);
      toast.success("Sharing link is live");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to start tunnel";
      setError(msg);
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
      toast.info("Sharing link revoked");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to stop tunnel");
    } finally {
      setBusy(false);
    }
  };

  const copy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied`);
    } catch {
      toast.error("Could not copy. Long-press the value above.");
    }
  };

  const active = status?.active === true;

  return (
    <>
      <SettingsSection
        title="Remote access (sharing)"
        icon="🔗"
        collapsible
        defaultOpen={false}
        help={{
          title: "Sharing",
          body: (
            <>
              Opens a public ngrok tunnel to this Nexus and gives you a URL plus
              a short access code. Open the URL on your phone (or share it),
              type the code on the phone's login screen, and the same UI as your
              desktop loads. Stop sharing to revoke instantly. The code is the
              credential — keep it private; the URL alone is harmless.
            </>
          ),
        }}
      >
        {error && <p className="settings-error">{error}</p>}

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* ngrok authtoken setup */}
          {!hasToken && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={{ fontSize: 13, opacity: 0.85 }}>
                ngrok authtoken
                <span style={{ opacity: 0.6, marginLeft: 6 }}>
                  (free at{" "}
                  <a
                    href="https://dashboard.ngrok.com/get-started/your-authtoken"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    dashboard.ngrok.com
                  </a>
                  )
                </span>
              </label>
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  type="password"
                  value={tokenInput}
                  placeholder="paste your authtoken"
                  onChange={(e) => setTokenInput(e.target.value)}
                  className="settings-input"
                  style={{ flex: 1 }}
                  disabled={busy}
                />
                <button
                  className="settings-btn settings-btn--primary"
                  onClick={handleSaveToken}
                  disabled={busy || !tokenInput.trim()}
                >
                  Save
                </button>
              </div>
            </div>
          )}

          {hasToken && !active && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {status && status.binary_installed === false && (
                <div
                  style={{
                    fontSize: 12,
                    padding: "8px 10px",
                    borderRadius: 6,
                    background: "rgba(120, 160, 255, 0.08)",
                    border: "1px solid rgba(120, 160, 255, 0.3)",
                  }}
                >
                  The ngrok binary isn't installed yet (~10 MB download). The
                  first activation will fetch it automatically, or you can do it
                  now.{" "}
                  <button
                    className="settings-btn"
                    style={{ marginLeft: 6 }}
                    onClick={handleInstall}
                    disabled={busy}
                  >
                    Install ngrok now
                  </button>
                </div>
              )}
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                  className="settings-btn settings-btn--primary"
                  onClick={() => setConfirmOpen(true)}
                  disabled={busy}
                >
                  Activate sharing link
                </button>
                <button
                  className="settings-btn"
                  onClick={handleClearToken}
                  disabled={busy}
                  title="Forget the saved ngrok authtoken"
                >
                  Reset token
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
                <strong style={{ color: "#ffb84d" }}>⚠ Public sharing is on.</strong>{" "}
                Your Nexus is reachable on the internet via ngrok. Anyone who
                knows <em>both</em> the URL and the access code below can read
                and write your vault, run agent turns, send messages, and use
                any tool the agent has — including web access and shell
                commands. Treat the code like a password. Stop sharing when
                you're done.
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
                      1. Open this URL on your phone:
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

                  {status.code && (
                    <div>
                      <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>
                        2. Type this access code on the phone:
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
                  )}

                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button
                      className="settings-btn"
                      onClick={() => status.share_url && copy(status.share_url, "URL")}
                      disabled={busy}
                    >
                      Copy URL
                    </button>
                    {status.code && (
                      <button
                        className="settings-btn"
                        onClick={() => copy(status.code!, "Code")}
                        disabled={busy}
                      >
                        Copy code
                      </button>
                    )}
                    <button
                      className="settings-btn settings-btn--danger"
                      onClick={handleStop}
                      disabled={busy}
                    >
                      Stop sharing
                    </button>
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 11, opacity: 0.6 }}>
                The URL is safe to share — only people who also know the code
                can open Nexus. Stopping sharing revokes both immediately.
              </div>
            </div>
          )}
        </div>
      </SettingsSection>

      {confirmOpen && (
        <Modal
          kind="confirm"
          title="Open public sharing link?"
          message={
            "This exposes your Nexus to the internet through an ngrok tunnel. " +
            "You'll get a URL plus a short code — anyone with both can use Nexus " +
            "as if they were on your desktop. Stop sharing to revoke."
          }
          confirmLabel="Open tunnel"
          danger
          onCancel={() => setConfirmOpen(false)}
          onSubmit={handleActivate}
        />
      )}
    </>
  );
}
