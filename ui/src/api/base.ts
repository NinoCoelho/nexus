/**
 * @file Base API URL configuration and Capacitor environment detection.
 *
 * The base URL is resolved in the following priority order:
 *  1. `VITE_NEXUS_API` (Vite build variable) — wins if defined.
 *  2. `window.__NEXUS_API__` — runtime override injected by Capacitor.
 *  3. Same origin when served by the backend (single-port deploy).
 *  4. `http://localhost:18989` as a fallback for the Vite dev server (port 1890).
 *
 * For Capacitor device builds, set `VITE_NEXUS_API` to point at the development
 * machine's IP — `localhost` on a device refers to itself, not the host machine.
 */
declare global {
  interface Window {
    __NEXUS_API__?: string;
  }
}

const runtime = typeof window !== "undefined" ? window.__NEXUS_API__ : undefined;
// When served from the backend (single-port deploy), default to same-origin so
// LAN IPs / custom hosts work. Fall back to localhost:18989 only for the Vite
// dev server (different port) and SSR.
const sameOrigin =
  typeof window !== "undefined" && window.location.protocol.startsWith("http")
    ? window.location.origin
    : "http://localhost:18989";
const devFallback =
  typeof window !== "undefined" && window.location.port === "1890"
    ? "http://localhost:18989"
    : sameOrigin;
/**
 * Base URL for all API calls. Never ends with a trailing slash.
 * Use this constant in all API modules instead of hard-coding the host.
 */
export const BASE = (import.meta.env.VITE_NEXUS_API as string | undefined)
  ?? runtime
  ?? devFallback;

/**
 * `true` when the app is running inside a native Capacitor shell.
 *
 * Used by API modules to choose between EventSource (web) and periodic polling:
 * `EventSource` over `capacitor://` is unreliable on iOS.
 */
export const IS_CAPACITOR =
  typeof window !== "undefined" &&
  // @ts-expect-error — Capacitor injects this global
  (typeof window.Capacitor !== "undefined") &&
  // @ts-expect-error — same
  window.Capacitor?.isNativePlatform?.() === true;

/**
 * Tunnel auth bootstrap.
 *
 * The new flow keeps secrets out of URLs entirely. The phone navigates to a
 * plain URL like `https://abc.ngrok-free.app/`, the SPA loads (the middleware
 * lets static UI through), and then we probe `/tunnel/auth-status`. If the
 * server reports `requires_redeem`, the app renders a login form where the
 * user types the short access code shown on the desktop. The form POSTs to
 * `/tunnel/redeem`, which validates the code and seats an HttpOnly cookie —
 * everything from that point on works exactly like a same-origin install.
 *
 * Code never touches the URL bar, browser history, or the ngrok request log.
 */

export interface AuthProbe {
  /** True only when the server says we're behind an active tunnel without a cookie. */
  requiresRedeem: boolean;
  /** True when the server is currently exposing itself via ngrok. */
  tunnelActive: boolean;
}

export async function probeTunnelAuth(): Promise<AuthProbe> {
  try {
    const res = await fetch(`${BASE}/tunnel/auth-status`, {
      credentials: "include",
    });
    if (!res.ok) {
      return { requiresRedeem: false, tunnelActive: false };
    }
    const data = await res.json();
    return {
      requiresRedeem: Boolean(data.requires_redeem),
      tunnelActive: Boolean(data.tunnel_active),
    };
  } catch {
    return { requiresRedeem: false, tunnelActive: false };
  }
}

export async function redeemTunnelCode(code: string): Promise<void> {
  const res = await fetch(`${BASE}/tunnel/redeem`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || "Invalid code");
  }
}
