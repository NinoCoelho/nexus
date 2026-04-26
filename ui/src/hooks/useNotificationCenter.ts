import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchNotificationHistory,
  subscribeGlobalNotifications,
  type HitlEventRow,
} from "../api";

interface UseNotificationCenterOptions {
  /** Keep history fresh whenever the bell is opened. */
  refreshOnOpen?: boolean;
  /** Limit passed to /notifications/history. */
  limit?: number;
}

interface UseNotificationCenterResult {
  history: HitlEventRow[];
  pendingCount: number;
  /** Refresh history immediately (e.g. on bell click). */
  refresh: () => void;
  /** True if the SW posted a message asking us to surface a specific request. */
  pendingFocusRequestId: string | null;
  clearPendingFocus: () => void;
}

/**
 * Maintains a refreshed view of the persisted HITL log for the bell.
 *
 * Re-fetches /notifications/history whenever a global HITL event fires
 * so the dropdown reflects the latest status without polling. Also
 * listens for `nx-hitl-incoming` messages from the service worker —
 * fired when a push lands on a focused tab or the user clicks an OS
 * notification — and exposes the request_id so the UI can hop the
 * approval queue to that specific prompt.
 */
export function useNotificationCenter(
  opts: UseNotificationCenterOptions = {},
): UseNotificationCenterResult {
  const limit = opts.limit ?? 50;
  const [history, setHistory] = useState<HitlEventRow[]>([]);
  const [pendingFocusRequestId, setPendingFocusRequestId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchNotificationHistory(limit)
      .then((rows) => setHistory(rows))
      .catch(() => {});
  }, [limit]);

  // Initial load + subscribe to global HITL events. Each event triggers
  // a refresh — cheap because the table is bounded to ~200 rows.
  useEffect(() => {
    refresh();
    const sub = subscribeGlobalNotifications(() => refresh());
    return () => sub.close();
  }, [refresh]);

  // Bridge: when the SW receives a push while a tab is focused (or the
  // user clicks an OS notification), it posts a message naming the
  // request_id. Surface it so the bell can jump to that prompt.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
      return;
    }
    const onMessage = (evt: MessageEvent) => {
      const data = evt.data as { type?: string; request_id?: string } | null;
      if (data?.type === "nx-hitl-incoming" && data.request_id) {
        setPendingFocusRequestId(data.request_id);
        refresh();
      }
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, [refresh]);

  // Also handle hash-/query-based deep links from notificationclick →
  // openWindow. e.g. /?respond=<rid>&session=<sid>
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const rid = params.get("respond");
    if (rid) {
      setPendingFocusRequestId(rid);
      // Strip the query so a refresh doesn't re-trigger.
      params.delete("respond");
      params.delete("session");
      const next = params.toString();
      const url = window.location.pathname + (next ? `?${next}` : "") + window.location.hash;
      window.history.replaceState(null, "", url);
    }
  }, []);

  const pendingCount = useMemo(
    () => history.filter((r) => r.status === "pending").length,
    [history],
  );

  const clearPendingFocus = useCallback(() => setPendingFocusRequestId(null), []);

  return { history, pendingCount, refresh, pendingFocusRequestId, clearPendingFocus };
}
