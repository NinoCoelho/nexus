import { useCallback, useEffect, useRef, useState } from "react";
import { Smartphone } from "lucide-react";
import { getPairingInfo, rotatePairingToken, type PairingInfo } from "../../api/pairing";
import { useToast } from "../../toast/ToastProvider";
import { useAuthState } from "../AuthGate";
import Modal from "../Modal";
import SettingsSection from "./SettingsSection";

/**
 * Native-app pairing (iOS client over the tailnet).
 *
 * Shows the QR the phone scans (a `nexus://pair` deep link carrying the
 * machine name, tailnet host, port and the pairing token). Loopback-only:
 * the section hides itself for tunnel-proxied sessions, matching the
 * route-level guard on /pair.
 */
export default function PairingSection() {
  const { proxied } = useAuthState();
  const toast = useToast();
  const [info, setInfo] = useState<PairingInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealToken, setRevealToken] = useState(false);
  const [rotateOpen, setRotateOpen] = useState(false);
  const qrCanvasRef = useRef<HTMLCanvasElement>(null);

  const refresh = useCallback(async () => {
    try {
      setInfo(await getPairingInfo());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pairing unavailable");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // QR encodes the deep link (not a URL the camera would open as a page) —
  // scanning it with the iOS camera launches the Nexus app directly into
  // its pairing confirm screen.
  useEffect(() => {
    const link = info?.deep_link;
    const canvas = qrCanvasRef.current;
    if (!link || !canvas) return;
    let cancelled = false;
    (async () => {
      try {
        const QR = (await import("qrcode")).default;
        if (cancelled) return;
        await QR.toCanvas(canvas, link, { width: 200, margin: 1 });
      } catch (e) {
        console.warn("QR render failed", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [info?.deep_link]);

  const handleRotate = async () => {
    setRotateOpen(false);
    setBusy(true);
    try {
      setInfo(await rotatePairingToken());
      toast.success("Token rotated — previously paired devices were revoked.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Rotation failed");
    } finally {
      setBusy(false);
    }
  };

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${what} copied`);
    } catch {
      toast.error("Copy failed");
    }
  };

  if (proxied) return null;

  const notBound = info?.host === "127.0.0.1";

  return (
    <>
      <SettingsSection
        title="Native app pairing"
        icon={<Smartphone size={16} />}
        collapsible
        defaultOpen={false}
        help={{
          title: "Pair the iOS app",
          body: (
            <>
              For owned devices on your tailnet (e.g. Tailscale). Set{" "}
              <code>[server] bind = "tailscale"</code> in{" "}
              <code>~/.nexus/config.toml</code> and restart the daemon, then
              scan this QR with the Nexus app (or the iOS camera). The phone
              connects directly over the tailnet — WireGuard-encrypted,
              token-authenticated. Rotate the token to revoke paired devices.
            </>
          ),
        }}
      >
        {error && <p className="settings-error">{error}</p>}

        {info && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {notBound && (
              <p
                className="settings-error"
                style={{
                  padding: "8px 12px",
                  borderRadius: 6,
                  border: "1px solid rgba(255, 170, 60, 0.35)",
                  background: "rgba(255, 170, 60, 0.08)",
                }}
              >
                Server is bound to loopback — the phone can't reach it yet. Add{" "}
                <code>[server] bind = "tailscale"</code> to{" "}
                <code>~/.nexus/config.toml</code> and restart{" "}
                (<code>nexus daemon restart</code>), then reopen this panel.
              </p>
            )}

            <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <canvas
                ref={qrCanvasRef}
                style={{ background: "var(--bg-panel)", borderRadius: 4, flexShrink: 0 }}
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
                  <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>Machine</div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{info.name}</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>Address</div>
                  <code
                    style={{
                      display: "block",
                      fontSize: 11,
                      wordBreak: "break-all",
                      userSelect: "all",
                      padding: "5px 7px",
                      background: "var(--code-bg, var(--bg-inset))",
                      borderRadius: 4,
                    }}
                  >
                    {info.url}
                  </code>
                </div>
                <div>
                  <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>Token</div>
                  <code
                    style={{
                      display: "block",
                      fontSize: 11,
                      wordBreak: "break-all",
                      userSelect: "all",
                      padding: "5px 7px",
                      background: "var(--code-bg, var(--bg-inset))",
                      borderRadius: 4,
                      filter: revealToken ? undefined : "blur(4px)",
                      cursor: "pointer",
                    }}
                    onClick={() => setRevealToken((v) => !v)}
                    title="Click to reveal/hide"
                  >
                    {info.token}
                  </code>
                </div>

                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button
                    className="settings-btn"
                    onClick={() => copy(info.deep_link, "Deep link")}
                  >
                    Copy link
                  </button>
                  <button
                    className="settings-btn"
                    onClick={() => copy(info.token, "Token")}
                  >
                    Copy token
                  </button>
                  <button
                    className="settings-btn settings-btn--danger"
                    onClick={() => setRotateOpen(true)}
                    disabled={busy}
                  >
                    Revoke paired devices
                  </button>
                </div>
              </div>
            </div>

            <div style={{ fontSize: 11, opacity: 0.6 }}>
              The QR carries the pairing token — treat it like a password.
              Tailscale encrypts the transport; the token authenticates the
              app.
            </div>
          </div>
        )}
      </SettingsSection>

      {rotateOpen && (
        <Modal
          kind="confirm"
          title="Revoke paired devices?"
          message="Every phone (or other client) paired with the current token will be
            disconnected immediately. They can re-pair by scanning the new QR."
          confirmLabel="Rotate token"
          danger
          onCancel={() => setRotateOpen(false)}
          onSubmit={handleRotate}
        />
      )}
    </>
  );
}
