// API client for chat: postChat, chatStream SSE parser, HITL session events.
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
  | { type: "tool"; name: string; args?: unknown; result_preview?: string }
  | { type: "done"; session_id: string; reply: string; trace: TraceEvent[]; skills_touched: string[]; model?: string; routed_by?: "user" | "auto" }
  | { type: "limit_reached"; iterations: number }
  | { type: "error"; detail: string; reason?: string; retryable?: boolean; status_code?: number | null };

export async function chatStream(
  message: string,
  session_id: string | undefined,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
  model?: string,
  routing_mode?: "fixed" | "auto",
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id, model, routing_mode }),
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
            routed_by: parsed.routed_by as "user" | "auto" | undefined,
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
export type SessionEvent =
  | { kind: "iter"; data: { n: number } }
  | { kind: "tool_call"; data: { name: string; args: unknown } }
  | { kind: "tool_result"; data: { name: string; preview: string } }
  | { kind: "reply"; data: { text: string } }
  | { kind: "user_request"; data: UserRequestPayload }
  | { kind: "user_request_auto"; data: { prompt: string; answer: string; reason: string } }
  | { kind: "user_request_cancelled"; data: { request_id: string; reason: string } };

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
