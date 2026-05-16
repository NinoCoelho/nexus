import { useCallback, useEffect, useRef, useState } from "react";
import {
  getNexusAccountStatus,
  refreshNexusAccount,
  logoutNexusAccount,
  type NexusAccountStatus,
} from "../api";
import { subscribeGlobalNotifications } from "../api/chat";

interface UseNexusAccountResult {
  status: NexusAccountStatus | null;
  loading: boolean;
  /** Manually re-fetch the cached status from the backend. */
  reload: () => Promise<void>;
  /** Force a /api/status hit and reload. */
  refresh: () => Promise<void>;
  /** Drop the apiKey + cached account record. */
  logout: () => Promise<void>;
}

/**
 * Polls / subscribes to the Nexus account state.
 *
 * Lightweight reads only — the apiKey lives in the Python backend and
 * never enters the browser. We pull the cached status from
 * ``GET /auth/nexus/status`` (no outbound call) and rely on the SSE
 * ``nexus_tier_changed`` event for live updates between polls.
 */
export function useNexusAccount(): UseNexusAccountResult {
  const [status, setStatus] = useState<NexusAccountStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const reload = useCallback(async () => {
    try {
      const next = await getNexusAccountStatus();
      if (mounted.current) setStatus(next);
    } catch {
      if (mounted.current) setStatus({ signedIn: false, email: "", tier: "free", cancelsAt: null, trialEnd: null, connected: false, models: [], refreshedAt: "" });
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await refreshNexusAccount();
      if (mounted.current) setStatus(next);
    } catch {
    }
  }, []);

  const logout = useCallback(async () => {
    await logoutNexusAccount();
    if (mounted.current) {
      setStatus({ signedIn: false, email: "", tier: "free", cancelsAt: null, trialEnd: null, connected: false, models: [], refreshedAt: "" });
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => {
      mounted.current = false;
    };
  }, [reload]);

  // Listen for tier-change events fanned out on the global notifications
  // channel and reload the cached status whenever one fires.
  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind === "nexus_tier_changed") {
        void reload();
      }
    });
    return () => sub.close();
  }, [reload]);

  // Periodically re-poll the cached status so spend gauges stay fresh
  // even between watcher ticks (the backend re-fetches /api/status
  // every poll_seconds, default 5 minutes).
  useEffect(() => {
    const id = setInterval(() => {
      void reload();
    }, 60_000);
    return () => clearInterval(id);
  }, [reload]);

  return { status, loading, reload, refresh, logout };
}
