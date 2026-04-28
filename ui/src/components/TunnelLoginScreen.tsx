/**
 * TunnelLoginScreen — the form a phone sees before it has a session cookie.
 *
 * The desktop UI shows a short access code in Settings → Sharing. The user
 * types it here (case-insensitive, dashes optional) and we POST it to
 * `/tunnel/redeem`. On success the server installs an HttpOnly cookie and we
 * tell the parent gate to remount <App />.
 */

import { useEffect, useRef, useState } from "react";
import { redeemTunnelCode } from "../api/base";

interface Props {
  onSuccess: () => void;
}

export default function TunnelLoginScreen({ onSuccess }: Props) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await redeemTunnelCode(code.trim());
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid code");
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--bg, #0e0e10)",
        color: "var(--fg, #eee)",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: "100%",
          maxWidth: 360,
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 12 }}>
          <h1 style={{ fontSize: 22, margin: "0 0 6px 0" }}>Nexus</h1>
          <div style={{ fontSize: 13, opacity: 0.7 }}>
            Enter the access code shown on your desktop.
          </div>
        </div>

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
          <strong style={{ color: "#ffb84d" }}>Heads up.</strong>{" "}
          You're connecting to a Nexus session over a Cloudflare tunnel. Only enter
          the code if you trust this URL and you (or someone you trust) just
          activated the link from the desktop app. Whoever holds the code can
          read your vault, send agent messages, and trigger tools. If you
          didn't expect this, close the tab.
        </div>

        <input
          ref={inputRef}
          type="text"
          autoComplete="one-time-code"
          inputMode="text"
          autoCapitalize="characters"
          autoCorrect="off"
          spellCheck={false}
          placeholder="ABCD-EFGH"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={busy}
          style={{
            fontSize: 22,
            letterSpacing: 4,
            textAlign: "center",
            padding: "14px 16px",
            borderRadius: 8,
            border: "1px solid var(--border, #333)",
            background: "var(--input-bg, #1a1a1d)",
            color: "inherit",
            fontFamily: "ui-monospace, monospace",
          }}
        />

        {error && (
          <div
            role="alert"
            style={{
              fontSize: 13,
              color: "var(--danger, #ff6b6b)",
              textAlign: "center",
            }}
          >
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !code.trim()}
          style={{
            padding: "12px 16px",
            borderRadius: 8,
            border: "none",
            background: busy ? "var(--btn-disabled, #444)" : "var(--accent, #4f8cff)",
            color: "white",
            fontSize: 15,
            cursor: busy ? "default" : "pointer",
          }}
        >
          {busy ? "Verifying…" : "Open Nexus"}
        </button>

        <div style={{ fontSize: 11, opacity: 0.5, textAlign: "center", marginTop: 8 }}>
          Open Nexus on your computer → Settings → Remote access. The 8-character
          code is shown there.
        </div>
      </form>
    </div>
  );
}
