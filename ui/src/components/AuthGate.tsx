/**
 * AuthGate — decides whether to render the full app or a login screen.
 *
 * On mount it asks the server `/tunnel/auth-status`. If the server says
 * the tunnel is active and we don't have a session cookie, we render the
 * <TunnelLoginScreen />. Otherwise we render whatever was passed as
 * children (the real app). The probe is async, so until it resolves we
 * show nothing — the SplashScreen mounted in main.tsx covers that gap
 * for the user.
 */

import { useEffect, useState, type ReactNode } from "react";
import { probeTunnelAuth } from "../api/base";
import TunnelLoginScreen from "./TunnelLoginScreen";

interface Props {
  children: ReactNode;
}

type State = "probing" | "authed" | "needs-redeem";

export default function AuthGate({ children }: Props) {
  const [state, setState] = useState<State>("probing");

  useEffect(() => {
    let cancelled = false;
    probeTunnelAuth().then((p) => {
      if (cancelled) return;
      setState(p.requiresRedeem ? "needs-redeem" : "authed");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "probing") return null;
  if (state === "needs-redeem") {
    return <TunnelLoginScreen onSuccess={() => setState("authed")} />;
  }
  return <>{children}</>;
}
