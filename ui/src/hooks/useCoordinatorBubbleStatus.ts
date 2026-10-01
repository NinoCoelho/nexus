/**
 * useCoordinatorBubbleStatus — live activity state for the coordinator
 * bubble, covering turns that did NOT start from this client:
 *
 * - processing: chat_turn jobs on the master session (job_started/job_done
 *   on the global notifications channel — every launch_turn registers one,
 *   including Telegram and sweep turns).
 * - hitl: the session's pending ask_user request (session-scoped events +
 *   `GET /chat/{sid}/pending` snapshot — authoritative even when the master
 *   is Telegram-routed and therefore filtered out of /notifications/pending).
 * - settledAt: timestamp of the last observed completion (job drain or
 *   voice_ack "complete") — drives the bubble's ✓ badge.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchPendingRequest,
  subscribeGlobalNotifications,
  subscribeSessionEvents,
  type SessionEvent,
  type UserRequestPayload,
} from "../api";

export interface CoordinatorBubbleStatus {
  /** A detached turn is running on the master session. */
  detached: boolean;
  /** Pending HITL request on the master session (null when none). */
  hitl: UserRequestPayload | null;
  /** Epoch ms of the last observed completion. */
  settledAt: number | null;
  /** Drop the local HITL mirror (after the bubble answered it inline). */
  clearHitl: () => void;
  /** Force a pending-request reconcile (e.g. after a turn settles). */
  refreshHitl: () => void;
}

export function useCoordinatorBubbleStatus(sessionId: string | null): CoordinatorBubbleStatus {
  const [detachedJobIds, setDetachedJobIds] = useState<Set<string>>(new Set());
  const [hitl, setHitl] = useState<UserRequestPayload | null>(null);
  const [settledAt, setSettledAt] = useState<number | null>(null);
  const hadJobsRef = useRef(false);

  const refreshHitl = useCallback(() => {
    if (!sessionId) return;
    fetchPendingRequest(sessionId)
      .then((req) => setHitl(req))
      .catch(() => {});
  }, [sessionId]);

  const clearHitl = useCallback(() => setHitl(null), []);

  // Global channel: job lifecycle (detached processing) + voice acks.
  useEffect(() => {
    if (!sessionId) {
      setDetachedJobIds(new Set());
      hadJobsRef.current = false;
      return;
    }
    const sub = subscribeGlobalNotifications((sid, event: SessionEvent) => {
      // Job events are published on the "__jobs__" pseudo-session — the
      // originating session rides inside `data.session_id` — so match on
      // the payload, not the envelope.
      if (event.kind === "job_started") {
        if (event.data.session_id !== sessionId) return;
        setDetachedJobIds((prev) => {
          const next = new Set(prev);
          next.add(event.data.id);
          return next;
        });
      } else if (event.kind === "job_done") {
        // Job ids are unique — matching our own set is precise enough.
        setDetachedJobIds((prev) => {
          if (!prev.has(event.data.job_id)) return prev;
          const next = new Set(prev);
          next.delete(event.data.job_id);
          return next;
        });
      } else if (event.kind === "voice_ack" && event.data.kind === "complete") {
        if (sid !== sessionId) return;
        setSettledAt(Date.now());
      }
    });
    return () => sub.close();
  }, [sessionId]);

  // Job drain → settled (only on the transition running → idle).
  useEffect(() => {
    const running = detachedJobIds.size > 0;
    if (running) {
      hadJobsRef.current = true;
    } else if (hadJobsRef.current) {
      hadJobsRef.current = false;
      setSettledAt(Date.now());
    }
  }, [detachedJobIds]);

  // Session channel: HITL lifecycle. `reply` (turn settled) also triggers
  // a pending reconcile in case a parked request outlived the turn.
  useEffect(() => {
    if (!sessionId) {
      setHitl(null);
      return;
    }
    setHitl(null);
    refreshHitl();
    const sub = subscribeSessionEvents(sessionId, (event: SessionEvent) => {
      if (event.kind === "user_request") {
        setHitl(event.data);
      } else if (event.kind === "user_request_cancelled") {
        setHitl((cur) => (cur && cur.request_id === event.data.request_id ? null : cur));
      } else if (event.kind === "user_request_auto") {
        // Auto-answered (YOLO) — data carries no request_id; drop whatever
        // we were mirroring.
        setHitl(null);
      } else if (event.kind === "turn_settled") {
        // Terminal marker for the turn (also fires for chained turns) —
        // reliable completion signal even when the job mapping missed.
        setSettledAt(Date.now());
        refreshHitl();
      }
    });
    return () => sub.close();
  }, [sessionId, refreshHitl]);

  return { detached: detachedJobIds.size > 0, hitl, settledAt, clearHitl, refreshHitl };
}
