// API client for session management.
import { BASE } from "./base";

export interface SessionSummary {
  id: string;
  title: string;
  // Backend sends unix seconds as integers. Declared as number|string to keep
  // callers defensive against an older backend that emitted ISO strings.
  created_at: number | string;
  updated_at: number | string;
  message_count: number;
}

export interface SessionMessage {
  seq?: number;
  role: "user" | "assistant" | "tool";
  /** Plain text in the common case. The server may also serialize a list of
   * ContentPart-shaped dicts for user turns that carried image/audio/
   * document attachments — consumers that need attachments should read the
   * raw value (``content as unknown``) and detect ``Array.isArray``. */
  content: string;
  tool_calls?: unknown;
  tool_call_id?: string;
  created_at: string;
  feedback?: "up" | "down" | null;
  pinned?: boolean;
}

export interface PinnedMessage {
  session_id: string;
  seq: number;
  role: string;
  content: string;
  created_at: string | null;
  session_title: string;
}

export async function setMessagePin(
  sessionId: string,
  seq: number,
  pinned: boolean,
): Promise<void> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/messages/${seq}/pin`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned }),
    },
  );
  if (!res.ok) throw new Error(`Pin error: ${res.status}`);
}

export interface ShareLink {
  token: string;
  path: string;
}

export interface SharedSession {
  title: string;
  shared_at: string;
  now: number;
  messages: { role: "user" | "assistant"; content: string; created_at: string | null }[];
}

export async function createSessionShare(sessionId: string): Promise<ShareLink> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/share`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Share error: ${res.status}`);
  return res.json();
}

export async function revokeSessionShare(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/share`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Share revoke error: ${res.status}`);
  }
}

export async function getSharedSession(token: string): Promise<SharedSession> {
  const res = await fetch(`${BASE}/share/${encodeURIComponent(token)}`);
  if (!res.ok) throw new Error(`Share read error: ${res.status}`);
  return res.json();
}

export async function listPins(limit = 50): Promise<PinnedMessage[]> {
  const res = await fetch(`${BASE}/pins?limit=${limit}`);
  if (!res.ok) throw new Error(`Pins error: ${res.status}`);
  return res.json();
}

export interface SessionDetail {
  id: string;
  title: string;
  context?: string;
  messages: SessionMessage[];
}

export interface SessionSearchResult {
  session_id: string;
  title: string;
  snippet: string;
}

export interface SessionToVaultResult {
  mode: "raw" | "summary";
  path: string;
  bytes?: number;
  length?: number;
}

/**
 * Ping the backend /health endpoint. Resolves true on a fast 200, false on
 * any failure (connection refused, 5xx, timeout). Used by the reachability
 * pill in the UI.
 */
export async function pingHealth(timeoutMs = 3000): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}/health`, { signal: ctrl.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function getHealth(): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

export async function getSessions(limit = 50): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE}/sessions?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Sessions error: ${res.status}`);
  return res.json();
}

export async function searchSessions(
  q: string,
  limit = 20,
): Promise<SessionSearchResult[]> {
  if (!q.trim()) return [];
  const res = await fetch(
    `${BASE}/sessions/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );
  if (!res.ok) throw new Error(`Search error: ${res.status}`);
  return res.json();
}

export async function getSession(id: string): Promise<SessionDetail> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Session error: ${res.status}`);
  return res.json();
}

export async function sessionToVault(
  id: string,
  mode: "raw" | "summary",
  path?: string,
): Promise<SessionToVaultResult> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}/to-vault`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, ...(path ? { path } : {}) }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `to-vault error: ${res.status}`);
  }
  return res.json();
}

export async function patchSession(id: string, patch: { title?: string }): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Session patch error: ${res.status}`);
}

export interface SessionUsage {
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  tool_call_count: number;
  estimated_cost_usd: number | null;
  cost_status: "ok" | "unknown" | "zero";
  context_window_tokens: number;
  estimated_context_tokens: number;
  context_pct: number;
  context_zone: "green" | "yellow" | "orange" | "red" | "unknown";
}

export async function getSessionUsage(sessionId: string): Promise<SessionUsage> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/usage`);
  if (!res.ok) throw new Error(`Usage error: ${res.status}`);
  return res.json();
}

export async function setMessageFeedback(
  sessionId: string,
  seq: number,
  value: "up" | "down" | null,
): Promise<void> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/messages/${seq}/feedback`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    },
  );
  if (!res.ok) throw new Error(`Feedback error: ${res.status}`);
}

export async function truncateSession(sessionId: string, beforeSeq: number): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ before_seq: beforeSeq }),
  });
  if (!res.ok) throw new Error(`Truncate error: ${res.status}`);
}

export interface CompactResult {
  compacted: number;
  saved_bytes: number;
  summarized: boolean;
  summarized_messages: number;
  messages_before: number;
  messages_after: number;
  tokens_before: number;
  tokens_after: number;
  zone_after: string;
  still_overflowed: boolean;
  budget_exceeded: boolean;
}

export async function compactSession(
  sessionId: string,
  options?: { model?: string; strategy?: string; force_summarize?: boolean },
): Promise<CompactResult> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
  if (!res.ok) throw new Error(`Compact error: ${res.status}`);
  return res.json();
}

export interface ContextStats {
  context_window_tokens: number;
  estimated_context_tokens: number;
  context_pct: number;
  context_zone: "green" | "yellow" | "orange" | "red" | "unknown";
  token_breakdown: { user: number; assistant: number; tool: number; system: number };
  message_counts: { user: number; assistant: number; tool: number; system: number };
  tool_stats: { name: string; call_count: number; estimated_tokens: number }[];
  compaction_hint: "none_needed" | "tools_only" | "summarize" | "full";
}

export async function getContextStats(sessionId: string): Promise<ContextStats> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/context-stats`);
  if (!res.ok) throw new Error(`Context stats error: ${res.status}`);
  return res.json();
}

export async function rollbackLastMessage(sessionId: string): Promise<{ removed_count: number; remaining_messages: number; removed_user_content: string | null }> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/messages/last`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Rollback error: ${res.status}`);
  return res.json();
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete error: ${res.status}`);
}

export async function exportSession(id: string): Promise<{ markdown: string; filename: string }> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}/export`);
  if (!res.ok) throw new Error(`Export error: ${res.status}`);
  const markdown = await res.text();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : `session-${id.slice(0, 8)}.md`;
  return { markdown, filename };
}

export async function importSession(markdown: string): Promise<{ id: string; title: string; imported_message_count: number }> {
  const res = await fetch(`${BASE}/sessions/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown }),
  });
  if (!res.ok) throw new Error(`Import error: ${res.status}`);
  return res.json();
}
