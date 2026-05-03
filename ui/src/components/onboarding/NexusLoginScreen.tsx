/**
 * NexusLoginScreen — first-run gate.
 *
 * Mandatory: shown until the user signs in to a Nexus account. The flow:
 *
 *   1. Click "Sign in" → opens https://www.nexus-model.us/auth/signin in a popup.
 *   2. The popup runs the Firebase sign-in dance and posts back
 *      `{ type: "nexus-auth-complete", idToken }` via window.opener.postMessage.
 *      The callback page sends with target origin "*", so we MUST validate
 *      `event.origin` against the configured website host ourselves.
 *   3. We forward the idToken to the loopback Python backend
 *      (POST /auth/nexus/verify). The backend exchanges it for the LiteLLM
 *      apiKey and stores it in ~/.nexus/secrets.toml — the apiKey never
 *      enters browser memory.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { verifyNexusIdToken } from "../../api";

interface Props {
  /** Website base URL — e.g. https://www.nexus-model.us. From cfg.nexus_account. */
  websiteUrl: string;
  /** Called after the backend confirms the apiKey is stored. */
  onSignedIn: () => void;
}

interface AuthMessage {
  type: "nexus-auth-complete";
  idToken: string;
}

function isAuthMessage(value: unknown): value is AuthMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return v.type === "nexus-auth-complete" && typeof v.idToken === "string" && v.idToken.length > 0;
}

function trustedOriginFor(websiteUrl: string): string {
  try {
    return new URL(websiteUrl).origin;
  } catch {
    return "https://www.nexus-model.us";
  }
}

export default function NexusLoginScreen({ websiteUrl, onSignedIn }: Props) {
  const { t } = useTranslation("settings");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<"idle" | "opening" | "verifying">("idle");
  const [error, setError] = useState<string | null>(null);

  const popupRef = useRef<Window | null>(null);
  const trustedOrigin = trustedOriginFor(websiteUrl);

  const cleanupPopupWatch = useRef<() => void>(() => {});

  const closePopup = useCallback(() => {
    cleanupPopupWatch.current();
    cleanupPopupWatch.current = () => {};
    if (popupRef.current && !popupRef.current.closed) {
      try {
        popupRef.current.close();
      } catch {
        // popup may have been closed by the user already
      }
    }
    popupRef.current = null;
  }, []);

  useEffect(() => closePopup, [closePopup]);

  const handleMessage = useCallback(
    async (event: MessageEvent) => {
      if (event.origin !== trustedOrigin) return;
      if (!isAuthMessage(event.data)) return;
      const idToken = event.data.idToken;
      closePopup();
      setStatus("verifying");
      setError(null);
      try {
        await verifyNexusIdToken(idToken);
        onSignedIn();
      } catch (err) {
        setStatus("idle");
        setBusy(false);
        setError(err instanceof Error ? err.message : t("settings:nexus.signIn.verifyFailed"));
      }
    },
    [closePopup, onSignedIn, t, trustedOrigin],
  );

  useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [handleMessage]);

  const startSignIn = useCallback(() => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setStatus("opening");

    const popupUrl = `${websiteUrl.replace(/\/$/, "")}/auth/signin?source=desktop`;
    const features = "popup,width=480,height=720,noopener=no,noreferrer=no";
    let popup: Window | null = null;
    try {
      popup = window.open(popupUrl, "nexus-signin", features);
    } catch {
      popup = null;
    }
    if (!popup) {
      setBusy(false);
      setStatus("idle");
      setError(t("settings:nexus.signIn.popupBlocked"));
      return;
    }
    popupRef.current = popup;
    try {
      popup.focus();
    } catch {
      // some browsers throw when a same-origin popup wasn't strictly opened
    }

    // Detect popup close: if the user dismisses without completing, drop
    // back to idle. Polled because there's no cross-origin "closed" event.
    const interval = window.setInterval(() => {
      if (!popupRef.current || popupRef.current.closed) {
        window.clearInterval(interval);
        cleanupPopupWatch.current = () => {};
        if (status !== "verifying") {
          setBusy(false);
          setStatus("idle");
          setError((prev) => prev ?? t("settings:nexus.signIn.popupClosed"));
        }
        popupRef.current = null;
      }
    }, 500);
    cleanupPopupWatch.current = () => window.clearInterval(interval);
  }, [busy, status, t, websiteUrl]);

  const buttonLabel =
    status === "opening"
      ? t("settings:nexus.signIn.opening")
      : status === "verifying"
        ? t("settings:nexus.signIn.verifying")
        : t("settings:nexus.signIn.primary");

  return (
    <div className="nexus-login-overlay" role="dialog" aria-modal="true">
      <div className="nexus-login-panel">
        <div className="nexus-login-header">
          <div className="nexus-login-mark">N</div>
          <div className="nexus-login-titles">
            <h1>{t("settings:nexus.signIn.title")}</h1>
            <p>{t("settings:nexus.signIn.subtitle")}</p>
          </div>
        </div>

        <div className="nexus-login-body">
          {error && (
            <div role="alert" className="nexus-login-error">
              {error}
            </div>
          )}

          <button
            type="button"
            className="nexus-login-primary"
            onClick={startSignIn}
            disabled={busy}
          >
            {buttonLabel}
          </button>

          <div className="nexus-login-footer">{t("settings:nexus.signIn.footer")}</div>
        </div>
      </div>
    </div>
  );
}
