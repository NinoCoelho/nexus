// API client for dispatching chat sessions from vault files or kanban cards.
import { BASE } from "./base";

export type DispatchMode = "chat" | "background" | "chat-hidden";

export interface DispatchResult {
  session_id: string;
  /** Seed text the caller should feed into /chat/stream. Absent for `background` mode. */
  seed_message?: string;
  path: string;
  card_id?: string | null;
  event_id?: string | null;
  mode?: DispatchMode;
  model?: string | null;
}

/**
 * Marker prefix the server uses on seeded user messages that should NOT
 * render as chat bubbles. The chat view filters messages whose content
 * starts with this string.
 */
export const HIDDEN_SEED_MARKER = "<!-- nx:hidden-seed -->\n";

export async function dispatchFromVault(
  body: { path: string; card_id?: string; event_id?: string; mode?: DispatchMode },
): Promise<DispatchResult> {
  const res = await fetch(`${BASE}/vault/dispatch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Dispatch error: ${res.status}`);
  return res.json();
}

export interface CardSession {
  id: string;
  title: string;
  model: string | null;
  updated_at: string;
}

export async function fetchCardSessions(
  path: string,
  card_id: string,
): Promise<CardSession[]> {
  const params = new URLSearchParams({ path, card_id });
  const res = await fetch(`${BASE}/vault/dispatch/sessions?${params}`);
  if (!res.ok) return [];
  return res.json();
}
