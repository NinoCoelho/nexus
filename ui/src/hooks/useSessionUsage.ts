import { useEffect, useState } from "react";
import { getSessionUsage, type SessionUsage } from "../api";

/**
 * Polls /sessions/{id}/usage at a low rate, with an extra refetch on
 * ``thinking`` falling edge so the status bar updates as soon as a turn
 * settles. Returns ``null`` while waiting and on errors (header just
 * hides instead of flashing).
 */
export function useSessionUsage(
  sessionId: string | null,
  thinking: boolean,
  pollMs = 8000,
): SessionUsage | null {
  const [usage, setUsage] = useState<SessionUsage | null>(null);

  useEffect(() => {
    if (!sessionId) { setUsage(null); return; }
    let cancelled = false;
    const fetchOnce = () => {
      getSessionUsage(sessionId)
        .then((u) => { if (!cancelled) setUsage(u); })
        .catch(() => { if (!cancelled) setUsage(null); });
    };
    fetchOnce();
    const id = setInterval(fetchOnce, pollMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [sessionId, pollMs]);

  // Refetch when a turn finishes streaming.
  useEffect(() => {
    if (!sessionId || thinking) return;
    let cancelled = false;
    getSessionUsage(sessionId)
      .then((u) => { if (!cancelled) setUsage(u); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [sessionId, thinking]);

  return usage;
}
