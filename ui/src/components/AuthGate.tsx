/**
 * AuthGate — decides whether to render the full app or a login screen.
 *
 * On mount it asks the server `/tunnel/auth-status`. If the server says
 * the tunnel is active and we don't have a session cookie, we render the
 * <TunnelLoginScreen />. Otherwise we render whatever was passed as
 * children (the real app). The probe is async, so until it resolves we
 * show nothing — the SplashScreen mounted in main.tsx covers that gap
 * for the user.
 *
 * Two side responsibilities:
 *   1. Listen for `AUTH_401_EVENT` from the global fetch interceptor. A
 *      stale or invalidated cookie sends the user back to the pairing
 *      screen instead of leaving them with a broken UI.
 *   2. Provide `proxied` via `AuthContext`. UI surfaces that only make
 *      sense for the loopback owner (e.g. the Sharing settings panel)
 *      hide themselves when `proxied` is true.
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { AUTH_401_EVENT, probeTunnelAuth } from "../api/base";
import TunnelLoginScreen from "./TunnelLoginScreen";

interface Props {
  children: ReactNode;
}

interface AuthState {
  /** True when the request reached the server through the tunnel (i.e. we are remote). */
  proxied: boolean;
}

const AuthContext = createContext<AuthState>({ proxied: false });

export function useAuthState(): AuthState {
  return useContext(AuthContext);
}

type State = "probing" | "authed" | "needs-redeem";

export default function AuthGate({ children }: Props) {
  const [state, setState] = useState<State>("probing");
  const [proxied, setProxied] = useState(false);

  // Initial probe.
  useEffect(() => {
    let cancelled = false;
    probeTunnelAuth().then((p) => {
      if (cancelled) return;
      setProxied(p.proxied);
      setState(p.requiresRedeem ? "needs-redeem" : "authed");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Re-probe on global 401: if the server now demands a redeem, drop back to
  // the pairing screen. Coalesce bursts of 401s (one per failing request) so
  // we don't spam the server with auth-status calls.
  useEffect(() => {
    let pending = false;
    const onUnauthorized = async () => {
      if (pending) return;
      pending = true;
      try {
        const p = await probeTunnelAuth();
        setProxied(p.proxied);
        if (p.requiresRedeem) setState("needs-redeem");
      } finally {
        pending = false;
      }
    };
    window.addEventListener(AUTH_401_EVENT, onUnauthorized);
    return () => window.removeEventListener(AUTH_401_EVENT, onUnauthorized);
  }, []);

  if (state === "probing") return null;
  if (state === "needs-redeem") {
    return (
      <TunnelLoginScreen
        onSuccess={() => {
          setState("authed");
          // Nothing else to do — the cookie is now seated. Subsequent fetches
          // succeed and the UI mounts normally.
        }}
      />
    );
  }
  return (
    <AuthContext.Provider value={{ proxied }}>{children}</AuthContext.Provider>
  );
}
