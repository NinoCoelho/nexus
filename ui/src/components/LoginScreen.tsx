import { useEffect, useRef, useState } from "react";

type Mode = "setup" | "invite" | "login";

interface Props {
  mode: Mode;
  inviteCode?: string;
  inviteRole?: string;
  tokenRequired?: boolean;
  onSuccess: () => void;
}

export default function LoginScreen({
  mode,
  inviteCode,
  inviteRole,
  tokenRequired = false,
  onSuccess,
}: Props) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [setupToken, setSetupToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "setup") {
        const { setupAccount } = await import("../api/auth");
        await setupAccount(setupToken, email, displayName);
      } else if (mode === "login") {
        const { loginWithEmail } = await import("../api/auth");
        await loginWithEmail(email, password);
      } else {
        const { registerWithInvite } = await import("../api/auth");
        await registerWithInvite(inviteCode || "", email, displayName, password || undefined);
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  };

  const titles: Record<Mode, string> = {
    setup: "Create Admin Account",
    invite: "Join Nexus",
    login: "Log In",
  };
  const subtitles: Record<Mode, string> = {
    setup: "Set up the administrator account for this Nexus instance.",
    invite: `Create your account (role: ${inviteRole || "member"})`,
    login: "Sign in with your email and password.",
  };

  const canSubmit =
    email.trim() &&
    (mode === "login" || displayName.trim()) &&
    (mode !== "setup" || !tokenRequired || setupToken.trim()) &&
    (mode !== "login" || password.length >= 6);

  const buttonText: Record<Mode, string> = {
    setup: busy ? "Creating..." : "Create Admin",
    invite: busy ? "Creating..." : "Join",
    login: busy ? "Signing in..." : "Sign In",
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--bg)",
        color: "var(--fg)",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: "100%",
          maxWidth: 380,
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 12 }}>
          <h1 style={{ fontSize: 22, margin: "0 0 6px 0" }}>{titles[mode]}</h1>
          <div style={{ fontSize: 13, opacity: 0.7 }}>{subtitles[mode]}</div>
        </div>

        {mode === "setup" && tokenRequired && (
          <input
            type="text"
            placeholder="Setup token (from 'nexus users admin-token')"
            value={setupToken}
            onChange={(e) => setSetupToken(e.target.value)}
            disabled={busy}
            required
            autoComplete="off"
            style={inputStyle}
          />
        )}

        <input
          ref={emailRef}
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={busy}
          required
          autoComplete="email"
          style={inputStyle}
        />

        {mode !== "login" && (
          <input
            type="text"
            placeholder="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            disabled={busy}
            required
            autoComplete="name"
            style={inputStyle}
          />
        )}

        <input
          type="password"
          placeholder={mode === "login" ? "Password" : "Password (for logging in from other devices)"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
          required={mode === "login"}
          minLength={mode === "login" ? 6 : undefined}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          style={inputStyle}
        />

        {mode === "invite" && (
          <div style={{ fontSize: 11, opacity: 0.5, textAlign: "center" }}>
            Set a password so you can log in from other browsers.
          </div>
        )}

        {error && (
          <div
            role="alert"
            style={{ fontSize: 13, color: "var(--bad)", textAlign: "center" }}
          >
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !canSubmit}
          style={{
            ...buttonStyle,
            background: busy || !canSubmit ? "var(--btn-disabled)" : "var(--accent)",
            cursor: busy ? "default" : "pointer",
          }}
        >
          {buttonText[mode]}
        </button>

        {mode === "setup" && tokenRequired && (
          <div style={{ fontSize: 11, opacity: 0.5, textAlign: "center", marginTop: 4 }}>
            Run `nexus users admin-token` on the server to get a setup token.
          </div>
        )}
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  fontSize: 15,
  padding: "12px 14px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--input-bg)",
  color: "inherit",
  width: "100%",
  boxSizing: "border-box",
};

const buttonStyle: React.CSSProperties = {
  padding: "12px 16px",
  borderRadius: 8,
  border: "none",
  color: "white",
  fontSize: 15,
};
