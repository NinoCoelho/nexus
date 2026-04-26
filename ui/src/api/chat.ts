/**
 * @file API client for the chat flow: SSE streaming, HITL, and session events.
 *
 * Exports three groups of functionality:
 * - `chatStream` / `postChat` — sending messages to the agent.
 * - `subscribeSessionEvents` — per-session SSE channel for HITL and trace events.
 * - `respondToUserRequest` / `fetchPendingRequest` — responding to HITL approvals.
 */
import { BASE, IS_CAPACITOR } from "./base";

export interface TraceEvent {
  iter: number;
  tool?: string;
  args?: unknown;
  result?: unknown;
  status?: "pending" | "done" | "error";
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  trace: TraceEvent[];
  skills_touched: string[];
}

export type StreamEvent =
  | { type: "delta"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool"; name: string; args?: unknown; result_preview?: string }
  | { type: "done"; session_id: string; reply: string; trace: TraceEvent[]; skills_touched: string[]; model?: string }
  | { type: "limit_reached"; iterations: number }
  | { type: "error"; detail: string; reason?: string; retryable?: boolean; status_code?: number | null };

/**
 * Send a message to the agent via `POST /chat/stream` and process the SSE response.
 *
 * The response body is consumed as a text stream; each SSE frame
 * (`event: <type>\ndata: <json>`) is parsed and dispatched to `onEvent`.
 * The Promise resolves only when the stream closes or is aborted via `signal`.
 *
 * @param message - The user's message text.
 * @param session_id - Existing session ID, or `undefined` to create a new session.
 * @param onEvent - Callback invoked for each received SSE event.
 * @param signal - `AbortSignal` to cancel the request (e.g. the Stop button).
 * @param model - Model identifier to use; omitting uses the server default.
 * @throws {Error} If the server returns a non-2xx status.
 */
export async function chatStream(
  message: string,
  session_id: string | undefined,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
  model?: string,
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id, model }),
    signal,
  });

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch { /* ignore */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE frames are separated by \n\n
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";

    for (const frame of frames) {
      if (!frame.trim()) continue;
      let eventName = "message";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLine = line.slice(5).trim();
        }
      }
      if (!dataLine) continue;
      try {
        const parsed = JSON.parse(dataLine) as Record<string, unknown>;
        if (eventName === "delta") {
          onEvent({ type: "delta", text: parsed.text as string });
        } else if (eventName === "thinking") {
          onEvent({ type: "thinking", text: (parsed.text as string) ?? "" });
        } else if (eventName === "tool") {
          onEvent({
            type: "tool",
            name: parsed.name as string,
            args: parsed.args,
            result_preview: parsed.result_preview as string | undefined,
          });
        } else if (eventName === "done") {
          const usage = parsed.usage as Record<string, unknown> | undefined;
          onEvent({
            type: "done",
            session_id: parsed.session_id as string,
            reply: parsed.reply as string,
            trace: (parsed.trace ?? []) as TraceEvent[],
            skills_touched: (parsed.skills_touched ?? []) as string[],
            model: (usage?.model ?? parsed.model) as string | undefined,
          });
        } else if (eventName === "limit_reached") {
          onEvent({ type: "limit_reached", iterations: (parsed.iterations as number) ?? 0 });
        } else if (eventName === "error") {
          onEvent({
            type: "error",
            detail: parsed.detail as string,
            reason: parsed.reason as string | undefined,
            retryable: parsed.retryable as boolean | undefined,
            status_code: parsed.status_code as number | null | undefined,
          });
        }
      } catch { /* malformed frame — skip */ }
    }
  }
}

/**
 * Send a message to the agent via `POST /chat` (synchronous, non-streaming mode).
 *
 * Suitable for non-UI integrations where waiting for the full response is acceptable.
 * For the interactive UI, prefer `chatStream`.
 *
 * @param message - The user's message text.
 * @param session_id - Existing session ID; omitting creates a new session.
 * @param context - Additional context prepended to the prompt server-side.
 * @returns Full response including `session_id`, `reply`, and `trace`.
 * @throws {Error} If the server returns a non-2xx status.
 */
export async function postChat(
  message: string,
  session_id?: string,
  context?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id, context }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      // body was not JSON; keep the status
    }
    throw new Error(detail);
  }
  return res.json();
}

// ── HITL (human-in-the-loop) ──────────────────────────────────────────────

export interface UserRequestPayload {
  request_id: string;
  prompt: string;
  kind: "confirm" | "choice" | "text" | "form";
  choices: string[] | null;
  default: string | null;
  timeout_seconds: number;
  fields?: import("../types/form").FieldSchema[];
  form_title?: string;
  form_description?: string;
}

/**
 * One event from the session-scoped SSE channel. Separate from the
 * existing `/chat/stream` events, which are per-turn content deltas.
 * Opened once per session on mount; carries every trace + HITL event
 * until the EventSource is closed.
 */
export type CalendarAlertPayload = {
  path: string;
  event_id: string;
  title: string;
  body?: string | null;
  start: string;
  end?: string | null;
  calendar_title?: string;
  all_day?: boolean;
};

export type SessionEvent =
  | { kind: "iter"; data: { n: number } }
  | { kind: "delta"; data: { text: string } }
  | { kind: "tool_call"; data: { name: string; args: unknown } }
  | { kind: "tool_result"; data: { name: string; preview: string } }
  | { kind: "reply"; data: { text: string } }
  | { kind: "user_request"; data: UserRequestPayload }
  | { kind: "user_request_auto"; data: { prompt: string; answer: string; reason: string } }
  | { kind: "user_request_cancelled"; data: { request_id: string; reason: string } }
  | { kind: "calendar_alert"; data: CalendarAlertPayload };

/** Returned from subscribeSessionEvents; close() ends the subscription. */
export interface SessionSubscription {
  close: () => void;
}

/**
 * Subscribe to a session's HITL/event stream.
 *
 * Uses EventSource on the web; falls back to polling /pending on
 * Capacitor (iOS WebView's EventSource over capacitor:// is unreliable)
 * or environments without EventSource. Polling only surfaces
 * `user_request` and `user_request_cancelled` — sufficient for HITL,
 * which is the only consumer at the moment.
 */
export function subscribeSessionEvents(
  session_id: string,
  onEvent: (event: SessionEvent) => void,
): SessionSubscription {
  if (IS_CAPACITOR || typeof EventSource === "undefined") {
    return pollSessionEvents(session_id, onEvent);
  }
  const url = `${BASE}/chat/${encodeURIComponent(session_id)}/events`;
  const es = new EventSource(url);

  const kinds: SessionEvent["kind"][] = [
    "iter",
    "delta",
    "tool_call",
    "tool_result",
    "reply",
    "user_request",
    "user_request_auto",
    "user_request_cancelled",
  ];
  for (const kind of kinds) {
    es.addEventListener(kind, (evt) => {
      try {
        const data = JSON.parse((evt as MessageEvent).data);
        onEvent({ kind, data } as SessionEvent);
      } catch {
        // Malformed server event — skip rather than crashing the UI.
      }
    });
  }

  return { close: () => es.close() };
}

function pollSessionEvents(
  session_id: string,
  onEvent: (event: SessionEvent) => void,
): SessionSubscription {
  let cancelled = false;
  let lastRequestId: string | null = null;
  const tick = async () => {
    if (cancelled) return;
    try {
      const req = await fetchPendingRequest(session_id);
      if (cancelled) return;
      if (req && req.request_id !== lastRequestId) {
        lastRequestId = req.request_id;
        onEvent({ kind: "user_request", data: req });
      } else if (!req && lastRequestId) {
        const prev = lastRequestId;
        lastRequestId = null;
        onEvent({
          kind: "user_request_cancelled",
          data: { request_id: prev, reason: "resolved" },
        });
      }
    } catch {
      // Network blip — try again next tick.
    }
  };
  void tick();
  const id = setInterval(tick, 2000);
  return {
    close: () => {
      cancelled = true;
      clearInterval(id);
    },
  };
}

/**
 * Poll `GET /chat/{session_id}/pending` to check for a waiting HITL approval.
 *
 * Used by the polling mechanism in environments without EventSource support (Capacitor/iOS).
 *
 * @param session_id - Session ID to query.
 * @returns The pending request payload, or `null` if there is none.
 */
export async function fetchPendingRequest(
  session_id: string,
): Promise<UserRequestPayload | null> {
  const res = await fetch(
    `${BASE}/chat/${encodeURIComponent(session_id)}/pending`,
  );
  if (!res.ok) return null;
  const body = (await res.json()) as { pending: UserRequestPayload | null };
  return body.pending ?? null;
}

/**
 * Submit the user's answer to a HITL request via `POST /chat/{session_id}/respond`.
 *
 * Objects (form responses of `kind: "form"`) are JSON-encoded before sending,
 * as expected by the backend. A 404 status is silently ignored — it means the
 * request expired or was cancelled before the answer arrived.
 *
 * @param session_id - Session ID where the request originated.
 * @param request_id - Unique HITL request ID from `UserRequestPayload.request_id`.
 * @param answer - User's answer: a string for simple kinds, an object for `kind: "form"`.
 * @throws {Error} If the server returns an error status other than 404.
 */
// ── Global HITL notifications ───────────────────────────────────────────
//
// /notifications/events fans out user_request* events from any session
// so the UI can pop a single approval dialog regardless of the active
// view. /notifications/pending is the reload-recovery snapshot.

/** Pending HITL request augmented with the originating session id. */
export type PendingNotification = UserRequestPayload & { session_id: string };

/**
 * Subscribe to the cross-session HITL notifications channel.
 *
 * Only ``user_request`` / ``user_request_auto`` /
 * ``user_request_cancelled`` events flow on this channel — non-HITL
 * activity (delta, tool_call, …) stays scoped to per-session
 * ``/chat/{sid}/events``. Capacitor environments fall back to polling
 * ``/notifications/pending`` every 2s.
 *
 * All callers share a single EventSource (refcounted) so multiple hooks
 * mounting in parallel don't burn through the browser's per-host
 * connection budget.
 */
type GlobalNotifListener = (sessionId: string, event: SessionEvent) => void;
const globalNotifListeners = new Set<GlobalNotifListener>();
let globalNotifSource: EventSource | null = null;

function openGlobalNotifSource(): void {
  if (globalNotifSource) return;
  const es = new EventSource(`${BASE}/notifications/events`);
  const kinds: SessionEvent["kind"][] = [
    "user_request",
    "user_request_auto",
    "user_request_cancelled",
    "calendar_alert",
  ];
  for (const kind of kinds) {
    es.addEventListener(kind, (evt) => {
      let session_id: string;
      let rest: Record<string, unknown>;
      try {
        const raw = JSON.parse((evt as MessageEvent).data) as Record<
          string,
          unknown
        > & { session_id: string };
        ({ session_id, ...rest } = raw);
      } catch {
        return;
      }
      const event = { kind, data: rest } as SessionEvent;
      for (const fn of globalNotifListeners) {
        try { fn(session_id, event); } catch { /* shield other listeners */ }
      }
    });
  }
  globalNotifSource = es;
}

export function subscribeGlobalNotifications(
  onEvent: GlobalNotifListener,
): SessionSubscription {
  if (IS_CAPACITOR || typeof EventSource === "undefined") {
    return pollGlobalNotifications(onEvent);
  }
  globalNotifListeners.add(onEvent);
  openGlobalNotifSource();
  return {
    close: () => {
      globalNotifListeners.delete(onEvent);
      if (globalNotifListeners.size === 0) {
        globalNotifSource?.close();
        globalNotifSource = null;
      }
    },
  };
}

function pollGlobalNotifications(
  onEvent: (sessionId: string, event: SessionEvent) => void,
): SessionSubscription {
  let cancelled = false;
  let lastSeen = new Set<string>();
  const tick = async () => {
    if (cancelled) return;
    try {
      const items = await fetchPendingNotifications();
      if (cancelled) return;
      const seen = new Set<string>();
      for (const it of items) {
        seen.add(it.request_id);
        if (!lastSeen.has(it.request_id)) {
          const { session_id, ...rest } = it;
          onEvent(session_id, {
            kind: "user_request",
            data: rest as UserRequestPayload,
          });
        }
      }
      for (const prev of lastSeen) {
        if (!seen.has(prev)) {
          // Original session is unknown post-resolution — scope-mismatch
          // is acceptable since the dialog only needs request_id to clear.
          onEvent("", {
            kind: "user_request_cancelled",
            data: { request_id: prev, reason: "resolved" },
          });
        }
      }
      lastSeen = seen;
    } catch {
      // Network blip — try again.
    }
  };
  void tick();
  const id = setInterval(tick, 2000);
  return {
    close: () => {
      cancelled = true;
      clearInterval(id);
    },
  };
}

/**
 * Snapshot every pending HITL request across all sessions.
 *
 * Used at app mount to recover any popup that fired while no global
 * subscriber was connected (cold tab / hard reload).
 */
export async function fetchPendingNotifications(): Promise<PendingNotification[]> {
  const res = await fetch(`${BASE}/notifications/pending`);
  if (!res.ok) return [];
  const body = (await res.json()) as { pending: PendingNotification[] };
  return body.pending ?? [];
}

export async function respondToUserRequest(
  session_id: string,
  request_id: string,
  answer: string | Record<string, unknown>,
): Promise<void> {
  // For form kind, answer is a dict; the backend's /respond expects a string
  // so we JSON-encode it. For all other kinds, answer is already a string.
  const encoded = typeof answer === "string" ? answer : JSON.stringify(answer);
  const res = await fetch(
    `${BASE}/chat/${encodeURIComponent(session_id)}/respond`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id, answer: encoded }),
    },
  );
  if (!res.ok && res.status !== 404) {
    // 404 is expected on a stale request (timed out, session reset) —
    // the UI treats it as a no-op and the dialog is already closed.
    throw new Error(`Respond error: ${res.status}`);
  }
}
