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
 * The flow keeps secrets out of URLs entirely. The phone navigates to a
 * plain URL like `https://words-here.trycloudflare.com/`, the SPA loads (the
 * middleware lets static UI through), and then we probe `/tunnel/auth-status`.
 * If the server reports `requires_redeem`, the app renders a login form where
 * the user types the short access code shown on the desktop. The form POSTs
 * to `/tunnel/redeem`, which validates the code and seats an HttpOnly cookie —
 * everything from that point on works exactly like a same-origin install.
 *
 * Code never touches the URL bar, browser history, or the tunnel request log.
 */

export interface AuthProbe {
  /** True only when the server says we're behind an active tunnel without a cookie. */
  requiresRedeem: boolean;
  /** True when the server is currently exposing itself via the tunnel. */
  tunnelActive: boolean;
  /** True when *this* request reached the server through the tunnel (i.e. we are the
   * remote redeemer, not the loopback owner). UI uses this to hide admin-only surfaces. */
  proxied: boolean;
}

export async function probeTunnelAuth(): Promise<AuthProbe> {
  try {
    // cache: "no-store" forces the request to bypass the browser's HTTP cache
    // entirely. iOS Safari otherwise (occasionally, depending on SW state and
    // history heuristics) serves a stale auth-status response, which would
    // mask token rotations the server already knows about.
    const res = await fetch(`${BASE}/tunnel/auth-status`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) {
      return { requiresRedeem: false, tunnelActive: false, proxied: false };
    }
    const data = await res.json();
    return {
      requiresRedeem: Boolean(data.requires_redeem),
      tunnelActive: Boolean(data.tunnel_active),
      proxied: Boolean(data.proxied),
    };
  } catch {
    return { requiresRedeem: false, tunnelActive: false, proxied: false };
  }
}

/**
 * Custom event fired when any API call to BASE returns 401. AuthGate listens
 * for this so a stale or invalidated cookie sends the user back to the pairing
 * screen instead of leaving them with a broken UI full of "unauthorized" errors.
 */
export const AUTH_401_EVENT = "nexus:auth-401";

// One-time global fetch interceptor. Two jobs:
//   1) Inject X-Locale on every request that targets our API base URL so the
//      backend can localize HTTP error messages without touching each call site.
//      Source-of-truth for the active language is i18next (which itself reads
//      ~/.nexus/config.toml via /config and the localStorage cache).
//   2) Watch responses for 401 and dispatch AUTH_401_EVENT so AuthGate can
//      re-probe. /tunnel/redeem is exempt — its 401 means "wrong code", which
//      the login form already surfaces.
if (typeof window !== "undefined" && !(window as any).__nexus401Patched) {
  (window as any).__nexus401Patched = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : (input as Request).url;
    const targetsApi = url.startsWith(BASE);
    let nextInit = init;
    if (targetsApi) {
      const lang =
        (typeof window !== "undefined" && (window as any).__nexusLanguage) ||
        (typeof localStorage !== "undefined" ? localStorage.getItem("nexus-language") : null);
      if (lang) {
        const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
        if (!headers.has("X-Locale")) headers.set("X-Locale", String(lang));
        nextInit = { ...(init ?? {}), headers };
      }
    }
    const res = await original(input as any, nextInit);
    try {
      if (
        res.status === 401 &&
        targetsApi &&
        !url.startsWith(`${BASE}/tunnel/redeem`)
      ) {
        window.dispatchEvent(new CustomEvent(AUTH_401_EVENT));
      }
    } catch {
      // never let the interceptor break the response chain
    }
    return res;
  };
}

export async function redeemTunnelCode(code: string): Promise<void> {
  const res = await fetch(`${BASE}/tunnel/redeem`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
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
