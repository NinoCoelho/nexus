import type React from "react";
import type { Message } from "../components/ChatView";
import type { SessionSummary } from "../api";

export type View = "chat" | "calendar" | "vault" | "kanban" | "data" | "graph" | "insights";

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
export function prettifyStreamError(detail: string): string {
  if (!detail) return "Something went wrong.";
  // Strip stray `b'...'` repr wrappers from older backends.
  const stripped = detail.replace(/b'([\s\S]*?)'$/, "$1");
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
  const allowed: View[] = ["chat", "calendar", "vault", "kanban", "data", "graph", "insights"];
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
  /** Placeholder session shown in the sidebar list while the first turn of a
   * brand-new chat is in flight (before the backend confirms creation). */
  pendingNewSession: SessionSummary | null;
  /** Ref for pending auto-send: { sid, seed } to fire after activeSession propagates. */
  pendingAutoSend: React.MutableRefObject<{ sid: string; seed: string } | null>;
  send: (override?: unknown) => Promise<void>;
  handleStop: () => void;
  handleRollback: (visibleIdx: number) => Promise<void>;
  handleContinue: () => void;
  handleContinuePartial: (visibleIdx: number) => void;
  handleRetryPartial: (visibleIdx: number) => Promise<void>;
  handleInputChange: (v: string) => void;
  handleAttachmentsChange: (files: { name: string; vaultPath: string }[]) => void;
  handleModelChange: (model: string) => void;
  handleSessionSelect: (id: string) => void;
  handleNewChat: () => void;
  loadSessionHistory: (id: string) => Promise<void>;
  patchState: (key: string, patch: Partial<ChatState>) => void;
  computeSeedModel: () => string;
  dismissLimitBanner: () => void;
}
