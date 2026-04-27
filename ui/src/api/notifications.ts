/**
 * @file API for the bell — durable HITL history + push subscription.
 *
 * The pending/cross-session HITL channel lives in chat.ts (it predates
 * this file). Everything here covers what's *new*: the persisted
 * history log and Web Push registration.
 */

import { BASE } from "./base";
import type { UserRequestPayload } from "./chat";

export type HitlEventStatus =
  | "pending"
  | "answered"
  | "auto_answered"
  | "cancelled"
  | "timed_out";

/** A row from /notifications/history — superset of UserRequestPayload + status. */
export interface HitlEventRow extends Omit<UserRequestPayload, "request_id"> {
  request_id: string;
  session_id: string;
  status: HitlEventStatus;
  answer: string | null;
  reason: string | null;
  created_at: string;
  resolved_at: string | null;
  /** Title of the originating session (null for sessions without one). */
  session_title: string | null;
  /** True when the row has a durable `hitl_pending` parked row backing it. */
  parked: boolean;
}

/** Cancel a specific HITL request. Routes to /respond's cancel sibling on the
 * server, which knows how to handle parked vs live differently. */
export async function cancelHitlRequest(
  session_id: string,
  request_id: string,
): Promise<void> {
  await fetch(
    `${BASE}/chat/${encodeURIComponent(session_id)}/hitl/${encodeURIComponent(request_id)}/cancel`,
    { method: "POST" },
  );
}

export async function fetchNotificationHistory(
  limit = 50,
): Promise<HitlEventRow[]> {
  const res = await fetch(
    `${BASE}/notifications/history?limit=${encodeURIComponent(limit)}`,
  );
  if (!res.ok) return [];
  const body = (await res.json()) as { history: HitlEventRow[] };
  return body.history ?? [];
}

// ── Web Push ─────────────────────────────────────────────────────────

export async function fetchVapidPublicKey(): Promise<string | null> {
  try {
    const res = await fetch(`${BASE}/push/vapid-public-key`);
    if (!res.ok) return null;
    const body = (await res.json()) as { public_key: string };
    return body.public_key;
  } catch {
    return null;
  }
}

export async function registerPushSubscription(
  sub: PushSubscription,
): Promise<void> {
  await fetch(`${BASE}/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  });
}

export async function deletePushSubscription(endpoint: string): Promise<void> {
  await fetch(`${BASE}/push/subscribe`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint }),
  });
}
