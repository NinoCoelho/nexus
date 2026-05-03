/**
 * NexusSignin — wizard step for the ``nexus_signin`` auth method.
 *
 * Opens a Firebase popup at the Nexus website's ``/auth/signin``,
 * receives the idToken via postMessage (with strict origin check),
 * forwards it to the loopback ``/auth/nexus/verify`` (which stores
 * the apiKey as ``secrets.nexus_api_key``), and reports success.
 *
 * The wizard parent then calls ``applyProviderWizard`` with
 * ``runtime_kind="nexus"`` to bind a provider entry against the
 * already-stored credential.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { verifyNexusIdToken } from "../../../api";

interface Props {
  websiteUrl: string;
  /** Called once the apiKey is stored locally (i.e. verify returned 2xx). */
  onSignedIn: () => void;
  /** Called when the user closes the popup without completing. */
  onCancel: () => void;
  /** True when the wizard is mid-applyProviderWizard so we can disable retry. */
  busy: boolean;
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

export default function NexusSignin({ websiteUrl, onSignedIn, onCancel, busy }: Props) {
  const { t } = useTranslation("settings");
  const [status, setStatus] = useState<"idle" | "opening" | "verifying">("idle");
  const [error, setError] = useState<string | null>(null);
  const popupRef = useRef<Window | null>(null);
  const watchRef = useRef<() => void>(() => {});
  const trustedOrigin = trustedOriginFor(websiteUrl);

  const closePopup = useCallback(() => {
    watchRef.current();
    watchRef.current = () => {};
    if (popupRef.current && !popupRef.current.closed) {
      try { popupRef.current.close(); } catch { /* already closed */ }
    }
    popupRef.current = null;
  }, []);

  useEffect(() => closePopup, [closePopup]);

  const handleMessage = useCallback(async (event: MessageEvent) => {
    if (event.origin !== trustedOrigin) return;
    if (!isAuthMessage(event.data)) return;
    closePopup();
    setStatus("verifying");
    setError(null);
    try {
      await verifyNexusIdToken(event.data.idToken);
      onSignedIn();
    } catch (err) {
      setStatus("idle");
      setError(err instanceof Error ? err.message : t("settings:nexus.signIn.verifyFailed"));
    }
  }, [closePopup, onSignedIn, t, trustedOrigin]);

  useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [handleMessage]);

  const startSignIn = useCallback(() => {
    setError(null);
    setStatus("opening");
    const popupUrl = `${websiteUrl.replace(/\/$/, "")}/auth/signin?source=desktop`;
    let popup: Window | null = null;
    try {
      popup = window.open(popupUrl, "nexus-signin", "popup,width=480,height=720");
    } catch {
      popup = null;
    }
    if (!popup) {
      setStatus("idle");
      setError(t("settings:nexus.signIn.popupBlocked"));
      return;
    }
    popupRef.current = popup;
    try { popup.focus(); } catch { /* same-origin oddity */ }

    const interval = window.setInterval(() => {
      if (!popupRef.current || popupRef.current.closed) {
        window.clearInterval(interval);
        watchRef.current = () => {};
        setStatus((prev) => (prev === "verifying" ? prev : "idle"));
        if (status !== "verifying") {
          setError((prev) => prev ?? t("settings:nexus.signIn.popupClosed"));
        }
        popupRef.current = null;
      }
    }, 500);
    watchRef.current = () => window.clearInterval(interval);
  }, [status, t, websiteUrl]);

  const buttonLabel =
    status === "opening"
      ? t("settings:nexus.signIn.opening")
      : status === "verifying" || busy
        ? t("settings:nexus.signIn.verifying")
        : t("settings:nexus.signIn.primary");

  return (
    <div className="provider-wizard-step provider-wizard-step--nexus-signin">
      <h3 className="provider-wizard-step__title">
        {t("settings:nexus.signIn.title")}
      </h3>
      <p className="provider-wizard-step__subtitle">
        {t("settings:nexus.signIn.subtitle")}
      </p>

      {error && (
        <div role="alert" className="provider-wizard-error">
          {error}
        </div>
      )}

      <button
        type="button"
        className="provider-wizard-primary-btn"
        onClick={startSignIn}
        disabled={status !== "idle" || busy}
        style={{ marginTop: 12 }}
      >
        {buttonLabel}
      </button>

      <button
        type="button"
        className="provider-wizard-secondary-btn"
        onClick={onCancel}
        disabled={busy}
        style={{ marginTop: 8 }}
      >
        Cancel
      </button>

      <div className="provider-wizard-step__hint" style={{ marginTop: 16 }}>
        {t("settings:nexus.signIn.footer")}
      </div>
    </div>
  );
}
