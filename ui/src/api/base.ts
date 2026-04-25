// Shared base URL for all API modules.
//
// Resolution order:
//   1. VITE_NEXUS_API (set at build time) — wins if defined.
//   2. window.__NEXUS_API__ — runtime override (Capacitor inject).
//   3. http://localhost:18989 in dev / web preview.
//
// On Capacitor (mobile), the bundled HTML is served from capacitor://
// or http://localhost on a random port — `localhost:18989` from the
// device points at the device itself, not the developer's machine.
// Set VITE_NEXUS_API at build time for device builds.
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
export const BASE = (import.meta.env.VITE_NEXUS_API as string | undefined)
  ?? runtime
  ?? devFallback;

// True when running inside a native Capacitor shell. EventSource over
// capacitor:// is unreliable on iOS, so callers switch to polling.
export const IS_CAPACITOR =
  typeof window !== "undefined" &&
  // @ts-expect-error — Capacitor injects this global
  (typeof window.Capacitor !== "undefined") &&
  // @ts-expect-error — same
  window.Capacitor?.isNativePlatform?.() === true;
