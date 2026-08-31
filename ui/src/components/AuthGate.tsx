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
  proxied: boolean;
}

const AuthContext = createContext<AuthState>({ proxied: false });

export function useAuthState(): AuthState {
  return useContext(AuthContext);
}

type TunnelState = "probing" | "authed" | "needs-redeem";

export default function AuthGate({ children }: Props) {
  const [tunnelState, setTunnelState] = useState<TunnelState>("probing");
  const [proxied, setProxied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    probeTunnelAuth().then((p) => {
      if (cancelled) return;
      setProxied(p.proxied);
      setTunnelState(p.requiresRedeem ? "needs-redeem" : "authed");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let pending = false;
    const onUnauthorized = async () => {
      if (pending) return;
      pending = true;
      try {
        const p = await probeTunnelAuth();
        setProxied(p.proxied);
        if (p.requiresRedeem) setTunnelState("needs-redeem");
      } finally {
        pending = false;
      }
    };
    window.addEventListener(AUTH_401_EVENT, onUnauthorized);
    return () => window.removeEventListener(AUTH_401_EVENT, onUnauthorized);
  }, []);

  if (tunnelState === "probing") return null;

  if (tunnelState === "needs-redeem") {
    return (
      <TunnelLoginScreen
        onSuccess={() => {
          setTunnelState("authed");
        }}
      />
    );
  }

  return (
    <AuthContext.Provider value={{ proxied }}>{children}</AuthContext.Provider>
  );
}
