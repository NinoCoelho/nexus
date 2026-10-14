// API client for the bots surface (projects exposed as bots + coordinator).

import { BASE } from "./base";

export interface Bot {
  id: string;
  kind: "coordinator" | "bot";
  name: string;
  description: string;
  persona: string;
  handle: string;
  color: string;
  icon: string;
  /** Master session id — only set on the coordinator ("main") entry. */
  session_id: string;
  /** Active (unarchived) chat count. */
  session_count: number;
  vault_path: string;
  created_at: number;
  updated_at: number;
}

export interface BotActivityOrigin {
  kind: string;
  source?: string;
  run_id?: string;
}

export interface BotActivity {
  id: string;
  title: string;
  context: string | null;
  origin: BotActivityOrigin | null;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface BotCleanupResult {
  kept: string[];
  archived: string[];
  dry_run: boolean;
}

export interface CleanupAllResult {
  archived: { id: string; title: string; bot_id: string | null }[];
  count: number;
  dry_run: boolean;
}

/** Group-chat membership stored on a session. */
export interface TeamInfo {
  name: string;
  members: string[];
  mode: "lead" | "round";
}

export interface DeleteBotResult {
  deleted: boolean;
  sessions_archived: number;
  sessions_deleted: number;
}

export type DeleteBotChats = "archive" | "delete" | "keep";

export async function getBots(): Promise<Bot[]> {
  const res = await fetch(`${BASE}/bots`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Bots error: ${res.status}`);
  return res.json();
}

export async function deleteBot(botId: string, chats: DeleteBotChats = "archive"): Promise<DeleteBotResult> {
  const res = await fetch(`${BASE}/bots/${encodeURIComponent(botId)}?chats=${chats}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Delete bot error: ${res.status}`);
  }
  return res.json();
}

export async function createTeam(body: {
  name: string;
  members: string[];
  mode: TeamInfo["mode"];
}): Promise<{ session_id: string; team: TeamInfo }> {
  const res = await fetch(`${BASE}/teams`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail ?? `Create team error: ${res.status}`);
  }
  return res.json();
}

export async function updateTeam(
  sessionId: string,
  body: { name?: string; members?: string[]; mode?: TeamInfo["mode"]; clear?: boolean },
): Promise<{ team: TeamInfo | null }> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/team`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail ?? `Update team error: ${res.status}`);
  }
  return res.json();
}

export async function getBotActivity(botId: string, limit = 100): Promise<BotActivity[]> {
  const res = await fetch(`${BASE}/bots/${encodeURIComponent(botId)}/activity?limit=${limit}`);
  if (!res.ok) throw new Error(`Bot activity error: ${res.status}`);
  return res.json();
}

export async function botCleanup(
  botId: string,
  options?: { keep?: number; dry_run?: boolean },
): Promise<BotCleanupResult> {
  const res = await fetch(`${BASE}/bots/${encodeURIComponent(botId)}/cleanup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep: options?.keep ?? 1, dry_run: options?.dry_run ?? false }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Bot cleanup error: ${res.status}`);
  }
  return res.json();
}

export async function cleanupAllBots(
  options?: { keep?: number; include_unprojected?: boolean; dry_run?: boolean },
): Promise<CleanupAllResult> {
  const res = await fetch(`${BASE}/bots/cleanup-all`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      keep: options?.keep ?? 1,
      include_unprojected: options?.include_unprojected ?? true,
      dry_run: options?.dry_run ?? false,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Cleanup error: ${res.status}`);
  }
  return res.json();
}
