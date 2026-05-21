import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import {
  probeAuthStatus,
  getSession,
  logout as apiLogout,
  type SessionInfo,
  type AuthStatus,
} from "../api/auth";
import { AUTH_401_EVENT } from "../api/base";

interface SessionState {
  loading: boolean;
  authStatus: AuthStatus | null;
  user: SessionInfo | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionState>({
  loading: true,
  authStatus: null,
  user: null,
  refresh: async () => {},
  logout: async () => {},
});

export function useSession() {
  return useContext(SessionContext);
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [user, setUser] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const status = await probeAuthStatus();
      setAuthStatus(status);
      if (status.multi_user && status.authenticated) {
        try {
          const session = await getSession();
          setUser(session);
        } catch {
          setUser(null);
        }
      } else {
        setUser(null);
      }
    } catch {
      setAuthStatus(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logoutFn = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // ignore
    }
    setUser(null);
    setAuthStatus((prev) =>
      prev ? { ...prev, authenticated: false } : prev,
    );
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onUnauthorized = () => {
      refresh();
    };
    window.addEventListener(AUTH_401_EVENT, onUnauthorized);
    return () => window.removeEventListener(AUTH_401_EVENT, onUnauthorized);
  }, [refresh]);

  return (
    <SessionContext.Provider
      value={{ loading, authStatus, user, refresh, logout: logoutFn }}
    >
      {children}
    </SessionContext.Provider>
  );
}
