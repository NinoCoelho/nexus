import { useCallback, useEffect, useState } from "react";
import {
  fetchPendingNotifications,
  respondToUserRequest,
  subscribeGlobalNotifications,
  type PendingNotification,
  type UserRequestPayload,
} from "../api";
import { sounds } from "./useSounds";

interface QueuedRequest {
  request: UserRequestPayload;
  session_id: string;
}

interface UseApprovalQueueResult {
  pendingRequest: UserRequestPayload | null;
  /** Total pending count, for the "1 of N" indicator on the dialog. */
  queueLength: number;
  handleApprovalSubmit: (answer: string | Record<string, unknown>) => Promise<void>;
  handleApprovalTimeout: () => void;
  clearPendingRequest: () => void;
  /** Move a specific pending request to the front of the queue. No-op if absent. */
  focusRequest: (request_id: string) => void;
}

/**
 * Manages the cross-session HITL approval queue.
 *
 * Subscribes once to ``/notifications/events`` so a popup appears for
 * any agent's question regardless of which view is active. Recovers
 * any pending requests that fired while no subscriber was connected
 * via ``/notifications/pending`` on mount. Multiple concurrent
 * requests are queued and surfaced one-at-a-time.
 */
export function useApprovalQueue(): UseApprovalQueueResult {
  const [queue, setQueue] = useState<QueuedRequest[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchPendingNotifications()
      .then((items: PendingNotification[]) => {
        if (cancelled) return;
        const recovered = items.map((it) => {
          const { session_id, ...rest } = it;
          return { session_id, request: rest as UserRequestPayload };
        });
        if (recovered.length === 0) return;
        setQueue((prev) => {
          const seen = new Set(prev.map((q) => q.request.request_id));
          const merged = [...prev];
          for (const r of recovered) {
            if (!seen.has(r.request.request_id)) merged.push(r);
          }
          return merged;
        });
      })
      .catch(() => {});

    const sub = subscribeGlobalNotifications((sessionId, event) => {
      if (event.kind === "user_request") {
        setQueue((prev) => {
          if (prev.some((q) => q.request.request_id === event.data.request_id)) {
            return prev;
          }
          sounds.popupOpen();
          return [...prev, { session_id: sessionId, request: event.data }];
        });
        return;
      }
      if (
        event.kind === "user_request_cancelled" ||
        event.kind === "user_request_auto"
      ) {
        const rid =
          event.kind === "user_request_cancelled"
            ? event.data.request_id
            : null;
        setQueue((prev) =>
          rid
            ? prev.filter((q) => q.request.request_id !== rid)
            : prev,
        );
      }
    });

    return () => {
      cancelled = true;
      sub.close();
    };
  }, []);

  const head = queue[0] ?? null;

  const handleApprovalSubmit = useCallback(
    async (answer: string | Record<string, unknown>) => {
      const item = head;
      if (!item) return;
      setQueue((prev) => prev.slice(1));
      try {
        await respondToUserRequest(item.session_id, item.request.request_id, answer);
      } catch {
        // Stale responses (404) are fine — dialog already closed.
      }
    },
    [head],
  );

  const handleApprovalTimeout = useCallback(() => {
    setQueue((prev) => prev.slice(1));
  }, []);

  const clearPendingRequest = useCallback(() => {
    setQueue([]);
  }, []);

  const focusRequest = useCallback((request_id: string) => {
    setQueue((prev) => {
      const idx = prev.findIndex((q) => q.request.request_id === request_id);
      if (idx <= 0) return prev;
      const next = [...prev];
      const [item] = next.splice(idx, 1);
      next.unshift(item);
      return next;
    });
  }, []);

  return {
    pendingRequest: head?.request ?? null,
    queueLength: queue.length,
    handleApprovalSubmit,
    handleApprovalTimeout,
    clearPendingRequest,
    focusRequest,
  };
}
