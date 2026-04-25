import { useCallback, useEffect, useState } from "react";
import {
  fetchPendingRequest,
  respondToUserRequest,
  subscribeSessionEvents,
  type UserRequestPayload,
} from "../api";

interface UseApprovalQueueResult {
  pendingRequest: UserRequestPayload | null;
  handleApprovalSubmit: (answer: string | Record<string, unknown>) => Promise<void>;
  handleApprovalTimeout: () => void;
  clearPendingRequest: () => void;
}

/**
 * Manages the HITL (human-in-the-loop) approval queue for a given session.
 * Opens an SSE EventSource before the first POST so first-turn approval
 * events are never missed. Recovers any pending request that was published
 * before the EventSource (re)opened.
 */
export function useApprovalQueue(hitlSessionId: string | null): UseApprovalQueueResult {
  const [pendingRequest, setPendingRequest] = useState<UserRequestPayload | null>(null);
  const [pendingRequestSession, setPendingRequestSession] = useState<string | null>(null);

  // Subscribe to the session's HITL event stream. The UI owns a
  // ``pendingSessionId`` for the not-yet-created "new chat" so the
  // EventSource can open before the first POST — no chicken-and-egg.
  // Once a real ``activeSession`` exists we prefer that.
  useEffect(() => {
    if (!hitlSessionId) return;

    // Recover any request that was published before the EventSource
    // (re)opened — the publish bus is fire-and-forget, so reload /
    // late subscribe / tab restore would otherwise miss the modal.
    let cancelled = false;
    fetchPendingRequest(hitlSessionId)
      .then((req) => {
        if (cancelled || !req) return;
        setPendingRequest(req);
        setPendingRequestSession(hitlSessionId);
      })
      .catch(() => {});

    const es = subscribeSessionEvents(hitlSessionId, (event) => {
      if (event.kind === "user_request") {
        setPendingRequest(event.data);
        setPendingRequestSession(hitlSessionId);
        return;
      }
      if (
        event.kind === "user_request_cancelled" ||
        event.kind === "user_request_auto"
      ) {
        setPendingRequest(null);
        setPendingRequestSession(null);
        return;
      }
      // iter / reply / tool_call / tool_result are already handled by
      // the /chat/stream POST response — ignore here to avoid
      // double-counting the activity strip.
    });
    return () => {
      cancelled = true;
      es.close();
    };
  }, [hitlSessionId]);

  const handleApprovalSubmit = useCallback(
    async (answer: string | Record<string, unknown>) => {
      const req = pendingRequest;
      const sid = pendingRequestSession;
      setPendingRequest(null);
      setPendingRequestSession(null);
      if (!req || !sid) return;
      try {
        await respondToUserRequest(sid, req.request_id, answer);
      } catch {
        // Stale responses (404) are fine — the dialog is already
        // closed. Any other error is rare enough to log and ignore.
      }
    },
    [pendingRequest, pendingRequestSession],
  );

  const handleApprovalTimeout = useCallback(() => {
    setPendingRequest(null);
    setPendingRequestSession(null);
  }, []);

  const clearPendingRequest = useCallback(() => {
    setPendingRequest(null);
    setPendingRequestSession(null);
  }, []);

  return { pendingRequest, handleApprovalSubmit, handleApprovalTimeout, clearPendingRequest };
}
