import { useCallback, useEffect, useState } from "react";
import {
  fetchVapidPublicKey,
  registerPushSubscription,
} from "../api";

export type PushPermission = "granted" | "denied" | "default" | "unsupported";

interface UsePushSubscriptionResult {
  permission: PushPermission;
  /** True once the SW is registered AND the browser holds a live subscription. */
  subscribed: boolean;
  /** Show user-facing prompt to enable notifications. Returns final permission. */
  requestPermission: () => Promise<PushPermission>;
}

/**
 * Registers the service worker and (if permission granted) subscribes to
 * Web Push so HITL prompts surface as OS notifications even when no
 * Nexus tab is open. Permission must be requested in response to a user
 * gesture — auto-prompting on mount tanks acceptance, so this hook
 * exposes ``requestPermission`` and lets a UI affordance trigger it.
 */
export function usePushSubscription(): UsePushSubscriptionResult {
  const [permission, setPermission] = useState<PushPermission>(() =>
    detectInitial(),
  );
  const [subscribed, setSubscribed] = useState(false);

  // Register the SW on mount regardless of permission — push is the
  // only consumer today, but the SW also relays notification clicks
  // back to a focused tab via postMessage (see sw.js), which is useful
  // even before the user grants permission.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
      return;
    }
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Vite dev server caches /public correctly; the only common
      // failure is being in an insecure context (non-localhost http).
    });
  }, []);

  // Once permission is granted, ensure we have an active subscription
  // that the backend knows about.
  useEffect(() => {
    if (permission !== "granted") return;
    let cancelled = false;
    void (async () => {
      try {
        if (!("serviceWorker" in navigator)) return;
        const reg = await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        if (!sub) {
          const publicKey = await fetchVapidPublicKey();
          if (!publicKey) return;
          sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            // Cast: PushManager.subscribe wants BufferSource, but TS's
            // narrow types reject Uint8Array<ArrayBufferLike> due to the
            // SharedArrayBuffer overlap. Runtime accepts it fine.
            applicationServerKey: urlBase64ToUint8Array(publicKey) as unknown as BufferSource,
          });
        }
        await registerPushSubscription(sub);
        if (!cancelled) setSubscribed(true);
      } catch {
        if (!cancelled) setSubscribed(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [permission]);

  const requestPermission = useCallback(async (): Promise<PushPermission> => {
    if (typeof Notification === "undefined") return "unsupported";
    const result = await Notification.requestPermission();
    setPermission(result as PushPermission);
    return result as PushPermission;
  }, []);

  // Re-check permission on focus — covers the case where the user
  // toggled it from the browser's site settings.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onFocus = () => setPermission(detectInitial());
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  return { permission, subscribed, requestPermission };
}

function detectInitial(): PushPermission {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission as PushPermission;
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const padded = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
  return out;
}
