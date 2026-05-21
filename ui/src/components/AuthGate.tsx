import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { AUTH_401_EVENT, probeTunnelAuth } from "../api/base";
import { getInviteInfo } from "../api/auth";
import TunnelLoginScreen from "./TunnelLoginScreen";
import LoginScreen from "./LoginScreen";
import { SessionProvider, useSession } from "./SessionProvider";

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

function InnerAuthGate({ children }: Props) {
  const { loading, authStatus, user, refresh } = useSession();

  const [tunnelState, setTunnelState] = useState<TunnelState>("probing");
  const [proxied, setProxied] = useState(false);
  const [inviteCode, setInviteCode] = useState<string | null>(null);
  const [inviteRole, setInviteRole] = useState<string>("member");

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
        await refresh();
      } finally {
        pending = false;
      }
    };
    window.addEventListener(AUTH_401_EVENT, onUnauthorized);
    return () => window.removeEventListener(AUTH_401_EVENT, onUnauthorized);
  }, [refresh]);

  useEffect(() => {
    if (authStatus?.multi_user && !authStatus.authenticated) {
      const path = window.location.pathname;
      const match = path.match(/^\/invite\/([^/]+)/);
      if (match) {
        setInviteCode(match[1]);
        getInviteInfo(match[1]).then((info) => setInviteRole(info.role)).catch(() => {});
      }
    }
  }, [authStatus]);

  if (tunnelState === "probing" || loading) return null;

  if (tunnelState === "needs-redeem") {
    return (
      <TunnelLoginScreen
        onSuccess={() => {
          setTunnelState("authed");
          refresh();
        }}
      />
    );
  }

  if (authStatus?.multi_user) {
    if (authStatus.needs_setup) {
      return (
        <LoginScreen
          mode="setup"
          tokenRequired={authStatus.setup_token_required}
          onSuccess={() => {
            refresh();
          }}
        />
      );
    }
    if (!authStatus.authenticated && !user) {
      if (inviteCode) {
        return (
          <LoginScreen
            mode="invite"
            inviteCode={inviteCode}
            inviteRole={inviteRole}
            onSuccess={() => {
              setInviteCode(null);
              refresh();
            }}
          />
        );
      }
      return (
        <LoginScreen
          mode="login"
          onSuccess={() => {
            refresh();
          }}
        />
      );
    }
  }

  return (
    <AuthContext.Provider value={{ proxied }}>{children}</AuthContext.Provider>
  );
}

export default function AuthGate({ children }: Props) {
  return (
    <SessionProvider>
      <InnerAuthGate>{children}</InnerAuthGate>
    </SessionProvider>
  );
}
