// API client for the Telegram bot runtime (poller status + live start/stop).
// The [telegram] config values themselves are edited via PATCH /config
// (see ./config); the bot token is stored via ./credentials.
import { BASE } from "./base";

export interface TelegramStatus {
  enabled: boolean;
  running: boolean;
  has_token: boolean;
  token_env: string;
  bot_username: string | null;
  allowed_user_ids: number[];
  allowlist_empty: boolean;
  /** Why the poller isn't delivering (e.g. "401 — re-save the token"), if any. */
  error: string | null;
}

async function _json(res: Response): Promise<any> {
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getTelegramStatus(): Promise<TelegramStatus> {
  return _json(await fetch(`${BASE}/telegram/status`));
}

export async function startTelegram(): Promise<TelegramStatus> {
  return _json(await fetch(`${BASE}/telegram/start`, { method: "POST" }));
}

export async function stopTelegram(): Promise<TelegramStatus> {
  return _json(await fetch(`${BASE}/telegram/stop`, { method: "POST" }));
}

export interface TelegramBindingInfo {
  chat_id: number;
  thread_id: number;
  kind: "dm" | "group" | "topic";
  project_id: string | null;
  project_name: string | null;
  active_session_id: string | null;
  active_session_title: string | null;
}

export async function getTelegramBindings(): Promise<TelegramBindingInfo[]> {
  return _json(await fetch(`${BASE}/telegram/bindings`));
}

export async function patchTelegramBinding(patch: {
  chat_id: number;
  thread_id?: number;
  project_id?: string | null;
  active_session_id?: string;
}): Promise<{ ok: boolean }> {
  return _json(
    await fetch(`${BASE}/telegram/bindings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  );
}

export async function deleteTelegramBinding(chat_id: number, thread_id: number = 0): Promise<{ ok: boolean }> {
  return _json(
    await fetch(`${BASE}/telegram/bindings`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id, thread_id }),
    }),
  );
}
