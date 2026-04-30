import { useEffect, useRef, useState } from "react";
import {
  pollOAuthFlow,
  startOAuthFlow,
  type AuthMethod,
  type OAuthStartResult,
  type ProviderCatalogEntry,
} from "../../../api";
import { useToast } from "../../../toast/ToastProvider";

interface Props {
  catalog: ProviderCatalogEntry;
  authMethod: AuthMethod;
  onComplete: (credentialRef: string) => void;
  onCancel: () => void;
}

const POLL_FALLBACK_INTERVAL_MS = 2500;

/**
 * Owns the device-code / redirect handshake. While the upstream user
 * authorizes the app, we poll /auth/oauth/poll on a per-flow interval.
 * Once the backend signals ``status: "ok"``, the credential is in the
 * secrets store under the returned name and the wizard advances.
 */
export default function OAuthInProgress({
  catalog,
  authMethod,
  onComplete,
  onCancel,
}: Props) {
  const toast = useToast();
  const [start, setStart] = useState<OAuthStartResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);

  // Kick off the flow once on mount. Guard with a ref so React strict-mode
  // doubles or step-back/forward don't spawn a second device-code request.
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const res = await startOAuthFlow(catalog.id, authMethod.id);
        if (cancelled) return;
        setStart(res);
        if (res.flow === "redirect") {
          window.open(res.authorize_url, "_blank", "noopener");
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [catalog.id, authMethod.id]);

  // Poll until completion. The interval comes from the device flow's
  // RFC-8628 `interval` field (most providers ask 5s); for redirect it
  // doesn't matter since the callback writes the bundle synchronously.
  useEffect(() => {
    if (!start) return;
    const intervalMs =
      start.flow === "device"
        ? Math.max(start.interval * 1000, 1000)
        : POLL_FALLBACK_INTERVAL_MS;
    let cancelled = false;
    let timer: number | null = null;

    async function tick() {
      if (cancelled || !start) return;
      try {
        const r = await pollOAuthFlow(start.session_id);
        if (cancelled) return;
        if (r.status === "ok" && r.credential_ref) {
          onComplete(r.credential_ref);
          return;
        }
        if (r.status === "error") {
          setError(r.error ?? "Sign-in failed.");
          return;
        }
        timer = window.setTimeout(tick, intervalMs);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    }

    timer = window.setTimeout(tick, intervalMs);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [start, onComplete]);

  function copyCode(code: string) {
    void navigator.clipboard
      .writeText(code)
      .then(() => toast.success("Code copied."))
      .catch(() => toast.warning("Could not copy — please copy manually."));
  }

  if (error) {
    return (
      <div className="provider-wizard-step provider-wizard-step--oauth">
        <h3 className="provider-wizard-step__title">Sign-in failed</h3>
        <p className="provider-wizard-step__subtitle">{error}</p>
        <button
          type="button"
          className="provider-wizard-secondary-btn"
          onClick={onCancel}
        >
          Back
        </button>
      </div>
    );
  }

  if (!start) {
    return (
      <div className="provider-wizard-step provider-wizard-step--oauth">
        <p className="provider-wizard-loading">Starting sign-in…</p>
      </div>
    );
  }

  if (start.flow === "device") {
    return (
      <div className="provider-wizard-step provider-wizard-step--oauth">
        <h3 className="provider-wizard-step__title">
          Sign in to {catalog.display_name}
        </h3>
        <p className="provider-wizard-step__subtitle">
          1. Open the URL below. 2. Enter the code. 3. Approve. We'll detect completion automatically.
        </p>
        <div className="provider-wizard-oauth-block">
          <a
            href={start.verification_uri}
            target="_blank"
            rel="noopener noreferrer"
            className="provider-wizard-oauth-link"
          >
            {start.verification_uri}
          </a>
          <div className="provider-wizard-oauth-code-row">
            <code className="provider-wizard-oauth-code">{start.user_code}</code>
            <button
              type="button"
              className="provider-wizard-secondary-btn"
              onClick={() => copyCode(start.user_code)}
            >
              Copy code
            </button>
          </div>
          <p className="provider-wizard-oauth-status">Waiting for sign-in…</p>
        </div>
        <button
          type="button"
          className="provider-wizard-secondary-btn"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    );
  }

  // redirect flow
  return (
    <div className="provider-wizard-step provider-wizard-step--oauth">
      <h3 className="provider-wizard-step__title">
        Sign in to {catalog.display_name}
      </h3>
      <p className="provider-wizard-step__subtitle">
        We opened the provider's sign-in page in a new tab. Complete sign-in there — we'll detect when it's done.
      </p>
      <div className="provider-wizard-oauth-block">
        <a
          href={start.authorize_url}
          target="_blank"
          rel="noopener noreferrer"
          className="provider-wizard-oauth-link"
        >
          Reopen sign-in page
        </a>
        <p className="provider-wizard-oauth-status">Waiting for sign-in…</p>
      </div>
      <button
        type="button"
        className="provider-wizard-secondary-btn"
        onClick={onCancel}
      >
        Cancel
      </button>
    </div>
  );
}
