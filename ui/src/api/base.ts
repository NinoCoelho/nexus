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
