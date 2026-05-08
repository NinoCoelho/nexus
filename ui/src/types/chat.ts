import type React from "react";
import type { Message } from "../components/ChatView";
import type { SessionSummary } from "../api";

export type View = "chat" | "calendar" | "vault" | "kanban" | "data" | "graph" | "insights" | "heartbeat";

/**
 * One entry per session the user has interacted with this tab. Keyed by
 * session id. "__new__" holds state for the not-yet-created session (first
 * message of a fresh chat). Lifted up here so nothing — view switches,
 * session switches, remounts — can drop a pending "thinking" indicator or
 * a half-typed message.
 */
export interface ChatState {
  messages: Message[];
  thinking: boolean;
  input: string;
  historyLoaded: boolean;
  attachments: { name: string; vaultPath: string }[];
  selectedModel?: string;
}

export const NEW_KEY = "__new__";

export function emptyState(): ChatState {
  return {
    messages: [],
    thinking: false,
    input: "",
    historyLoaded: true,
    attachments: [],
  };
}

export function parseHistoryTimestamp(raw: unknown): Date {
  if (raw == null) return new Date();
  if (typeof raw === "number") return new Date(raw * 1000);
  const parsed = new Date(raw as string);
  return isNaN(parsed.getTime()) ? new Date() : parsed;
}

/**
 * Turn a raw upstream transport error into a human-friendly message.
 *
 * Input shape (after backend hardening, see llm.py):
 *   HTTP 500: {"error":{"code":"1234","message":"Network error, error id: ..., please try again later"}}
 *
 * We try to extract the nested provider message; if that fails we fall back
 * to the raw detail. Kept intentionally forgiving — any unexpected shape
 * still produces a readable line.
 */
/** Lift the upstream-provider's ``error.message`` out of an SDK
 *  exception body, even when the body is a Python-repr (single quotes,
 *  embedded None) instead of strict JSON. Returns the message string,
 *  or empty when nothing useful is in there.
 *
 *  Three passes, in order:
 *  1. Strict JSON parse (succeeds when the SDK kept double quotes).
 *  2. Repr → JSON (single → double quotes, None → null) and parse.
 *  3. Regex grab — handles weird quote-mixing the previous passes can't
 *     parse, e.g. inner text with apostrophes inside a single-quoted dict.
 */
function extractInnerMessage(body: string): string {
  // Pass 1
  try {
    const parsed = JSON.parse(body);
    const msg = parsed?.error?.message ?? parsed?.message ?? parsed?.detail;
    if (typeof msg === "string" && msg.trim()) return msg.trim();
  } catch {
    // fall through
  }

  // Pass 2: cheap Python-repr → JSON pass. Only safe enough when the
  // string itself doesn't contain awkward quoting; pass 3 catches the rest.
  try {
    const reprAsJson = body
      .replace(/\bNone\b/g, "null")
      .replace(/\bTrue\b/g, "true")
      .replace(/\bFalse\b/g, "false")
      // Greedy double→single swap. Risky for strings with embedded
      // quotes; protected by the try.
      .replace(/'/g, '"');
    const parsed = JSON.parse(reprAsJson);
    const msg = parsed?.error?.message ?? parsed?.message ?? parsed?.detail;
    if (typeof msg === "string" && msg.trim()) return msg.trim();
  } catch {
    // fall through
  }

  // Pass 3: lift the value of `'message': "..."` or `"message": "..."`
  // via regex. Tolerant to either-quote dict + either-quote string.
  const m = body.match(/['"]message['"]\s*:\s*(["'])((?:\\\1|.)*?)\1/);
  if (m && m[2]) {
    return m[2]
      .replace(/\\"/g, '"')
      .replace(/\\'/g, "'")
      .trim();
  }
  return "";
}


/** One-liner per classified reason. The agent loop forwards loom's
 *  classify_api_error() reason verbatim through the SSE error event;
 *  this is the user-facing translation. */
const REASON_TO_BANNER: Record<string, string> = {
  rate_limit: "Provider rate limit hit. Wait a minute, or switch to a smaller / different model.",
  auth_error: "Authentication failed — check the API key or re-run the wizard.",
  bad_request: "Provider rejected the request — usually a model name, parameter, or message-shape issue.",
  transport: "Couldn't reach the provider — check your network or the base URL.",
  upstream_timeout: "Provider took too long to respond. Try again, or reduce context size.",
  context_overflow: "Conversation is too long for the model's context window. Compact history or start a new session.",
  message_too_large: "This message is too large to send. Try shortening it or compacting the conversation first.",
  empty_response: "The model returned an empty response. Sometimes a retry helps.",
  length: "The response was cut off because it hit the model's output limit.",
  iteration_limit: "Agent stopped after hitting the per-turn iteration limit.",
  llm_error: "Provider error.",
  hook_error: "Pre-call hook failed before the model was invoked.",
};

/** Decide whether an upstream-extracted message is meaty enough to show
 *  verbatim, or whether we should prefer our own reason-based banner.
 *  Anthropic's 429 body has ``message: "Error"`` (literally just that
 *  word) — useless. Their 400 quota body has the actual actionable
 *  sentence. The heuristic: 20+ chars and not a placeholder. */
function isSubstantiveMessage(s: string): boolean {
  const t = s.trim();
  if (t.length < 20) return false;
  if (/^error$/i.test(t)) return false;
  return true;
}

export function prettifyStreamError(detail: string, reason?: string): string {
  // 1. Try to lift the upstream provider's actual ``error.message`` —
  //    when it's a real sentence (not just "Error"), it's almost
  //    always more actionable than our generic banner. Examples we've
  //    seen in the wild: "You're out of extra usage. Add more at
  //    claude.ai/settings/usage", "model 'gpt-5.4' does not exist",
  //    "context length 131072 exceeded". Fall back to the reason
  //    banner only when extraction is empty or returns junk.
  if (detail) {
    const stripped0 = detail.replace(/b'([\s\S]*?)'$/, "$1");
    const m = stripped0.match(/^Error code:\s+\d+\s*-\s*(.+)$/s)
      || stripped0.match(/^HTTP\s+\d+:\s*(.+)$/s);
    if (m) {
      const inner = extractInnerMessage(m[1].trim());
      if (inner && isSubstantiveMessage(inner)) return inner;
    }
  }

  // 2. Fall back to the classified reason banner — that's the right
  //    answer when the upstream message is generic (e.g. 429 with
  //    body just `{"message":"Error"}`).
  if (reason && REASON_TO_BANNER[reason]) {
    return REASON_TO_BANNER[reason];
  }

  if (!detail) return "Something went wrong.";

  // 2. Strip stray `b'...'` repr wrappers from older backends.
  const stripped = detail.replace(/b'([\s\S]*?)'$/, "$1");

  // 3. Anthropic SDK shape: `Error code: NNN - {...JSON-ish dict...}`.
  //    The body is a Python-repr of the parsed JSON (single quotes,
  //    `None` instead of `null`). We try a JSON parse first, then a
  //    repr → JSON conversion, then a regex grab. The goal is to lift
  //    the upstream's `error.message` verbatim — that's the actually
  //    useful sentence (e.g. "You're out of extra usage. Add more at
  //    claude.ai/settings/usage and keep going.") that we don't want
  //    to flatten into a generic "bad request" banner.
  const anthropicMatch = stripped.match(/^Error code:\s+(\d+)\s*-\s*(.+)$/s);
  if (anthropicMatch) {
    const status = anthropicMatch[1];
    const body = anthropicMatch[2].trim();
    const inner = extractInnerMessage(body);
    if (inner) return `${inner} (HTTP ${status})`;
    if (status === "429") return REASON_TO_BANNER.rate_limit;
    if (status === "401" || status === "403") return REASON_TO_BANNER.auth_error;
    if (status === "400") return REASON_TO_BANNER.bad_request;
    if (status.startsWith("5")) return "Provider returned a server error — try again in a minute.";
    return `Provider error (HTTP ${status}).`;
  }

  // 4. Standard `HTTP NNN: ...` shape (OpenAI-compat etc.).
  const httpMatch = stripped.match(/^HTTP\s+(\d+):\s*(.+)$/s);
  if (httpMatch) {
    const status = httpMatch[1];
    const body = httpMatch[2].trim();
    try {
      const parsed = JSON.parse(body);
      const msg =
        parsed?.error?.message ??
        parsed?.message ??
        parsed?.detail;
      if (typeof msg === "string" && msg.length > 0) {
        return `Upstream provider error (HTTP ${status}): ${msg}`;
      }
    } catch {
      // body wasn't JSON — fall through
    }
    return `Upstream provider error (HTTP ${status}). ${body.slice(0, 180)}`;
  }

  return detail;
}

export function freshSessionId(): string {
  const raw =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return raw.replace(/-/g, "");
}

/** Parse ?view=vault&path=... deep link on first mount. */
export function readInitialView(): { view: View; vaultPath: string | null } {
  if (typeof window === "undefined") return { view: "chat", vaultPath: null };
  const qs = new URLSearchParams(window.location.search);
  const v = qs.get("view");
  const path = qs.get("path");
  const allowed: View[] = ["chat", "calendar", "vault", "kanban", "data", "graph", "insights", "heartbeat"];
  const view = (allowed as string[]).includes(v ?? "") ? (v as View) : "chat";
  return { view, vaultPath: path };
}

/** Return type of useChatSession — exported here to avoid circular imports. */
export interface UseChatSessionResult {
  chatStates: Map<string, ChatState>;
  setChatStates: React.Dispatch<React.SetStateAction<Map<string, ChatState>>>;
  activeKey: string;
  activeState: ChatState;
  activeSession: string | null;
  setActiveSession: (id: string | null) => void;
  pendingSessionId: string;
  setPendingSessionId: (id: string) => void;
  sessionsRevision: number;
  setSessionsRevision: React.Dispatch<React.SetStateAction<number>>;
  pendingNewSession: SessionSummary | null;
  pendingAutoSend: React.MutableRefObject<{ sid: string; seed: string } | null>;
  send: (override?: unknown) => Promise<void>;
  handleStop: () => void;
  handleRollback: (visibleIdx: number) => Promise<void>;
  handleContinuePartial: (visibleIdx: number) => void;
  handleRetryPartial: (visibleIdx: number) => Promise<void>;
  handleInputChange: (v: string) => void;
  handleAttachmentsChange: (files: { name: string; vaultPath: string }[]) => void;
  handleModelChange: (model: string) => void;
  handleSessionSelect: (id: string) => void;
  handleNewChat: () => void;
  loadSessionHistory: (id: string) => Promise<void>;
  patchState: (key: string, patch: Partial<ChatState>) => void;
  computeSeedModel: (preferred?: string) => string;
  handleCompact: () => Promise<{ compacted: number; saved_bytes: number } | undefined>;
  handleRemoveLast: () => Promise<void>;
}
