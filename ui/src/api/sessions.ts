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
  content: string;
  tool_calls?: unknown;
  tool_call_id?: string;
  created_at: string;
  feedback?: "up" | "down" | null;
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
  const res = await fetch(`${BASE}/sessions?limit=${limit}`);
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

export async function patchSession(id: string, patch: { title?: string }): Promise<SessionDetail> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Session patch error: ${res.status}`);
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

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
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
