/**
 * useMissedTasks — fetches calendar events flagged ``missed`` (past-due while
 * the computer/server was offline) and exposes them for the recovery prompt.
 *
 * Fetches on:
 *  - app mount (covers server startup / "computer was off")
 *  - ``online`` event (network restore)
 *  - ``visibilitychange`` when the document becomes visible (laptop wake)
 *
 * Dismissed event ids are tracked in ``sessionStorage`` so the same set is not
 * re-prompted after a sleep/wake cycle within the same session. Only newly-
 * missed ids (not previously dismissed) surface in ``missed``.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { listMissedEvents, type MissedEvent } from "../api/calendar";

const DISMISSED_KEY = "nexus:missed-dismissed";

function loadDismissed(): Set<string> {
  try {
    const raw = sessionStorage.getItem(DISMISSED_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

function saveDismissed(ids: Set<string>): void {
  try {
    sessionStorage.setItem(DISMISSED_KEY, JSON.stringify([...ids]));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

export function useMissedTasks() {
  const [missed, setMissed] = useState<MissedEvent[]>([]);
  const dismissedRef = useRef<Set<string>>(loadDismissed());
  const inFlight = useRef(false);

  const refresh = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    listMissedEvents()
      .then(({ events }) => {
        const dismissed = dismissedRef.current;
        setMissed(events.filter((e) => !dismissed.has(e.id)));
      })
      .catch(() => {
        /* server not ready yet — will retry on next trigger */
      })
      .finally(() => {
        inFlight.current = false;
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onOnline = () => refresh();
    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  const dismissAll = useCallback(() => {
    setMissed((prev) => {
      const next = new Set(dismissedRef.current);
      for (const e of prev) next.add(e.id);
      dismissedRef.current = next;
      saveDismissed(next);
      return [];
    });
  }, []);

  const removeOne = useCallback((eventId: string) => {
    setMissed((prev) => prev.filter((e) => e.id !== eventId));
  }, []);

  return { missed, refresh, dismissAll, removeOne };
}
