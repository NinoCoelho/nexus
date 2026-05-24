import { BASE } from "./base";
import type { EventStatus } from "./calendar";

export interface HeartbeatRun {
  id: string;
  name: string;
  description: string;
  schedule: string;
  enabled: boolean;
  instructions: string;
  health: "healthy" | "error" | "idle" | "unknown";
  last_check: string | null;
  last_fired: string | null;
  last_error: string | null;
  next_due: string | null;
  state: Record<string, unknown>;
}

export interface HeartbeatListResult {
  heartbeats: HeartbeatRun[];
  scheduler_running: boolean;
  tick_interval: number | null;
}

export interface HeartbeatEvent {
  event_id: string;
  title: string;
  start: string;
  end?: string | null;
  status: EventStatus;
  session_id: string | null;
  rrule: string | null;
  trigger: string;
  all_day: boolean;
  fire_from: string | null;
  fire_to: string | null;
  fire_every_min: number | null;
  assignee: string | null;
  model: string | null;
  remind_before_min: number | null;
  calendar_path: string;
  calendar_title: string;
  calendar_tz: string;
  body: string;
}

export interface FireLogEntry {
  id: number;
  timestamp: string | null;
  event_id: string;
  event_title: string;
  calendar_path: string;
  session_id: string | null;
  status: "running" | "done" | "failed";
  error: string | null;
  duration_ms: number | null;
}

async function jsonOrThrow<T>(res: Response, label: string): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `${label}: ${res.status}`);
  }
  return res.json();
}

export async function listHeartbeats(): Promise<HeartbeatListResult> {
  const res = await fetch(`${BASE}/heartbeat`);
  return jsonOrThrow<HeartbeatListResult>(res, "Heartbeat list error");
}

export async function getHeartbeat(id: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/heartbeat/${encodeURIComponent(id)}`);
  return jsonOrThrow<Record<string, unknown>>(res, "Heartbeat detail error");
}

export async function patchHeartbeat(id: string, enabled: boolean): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${BASE}/heartbeat/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return jsonOrThrow(res, "Heartbeat patch error");
}

export async function triggerHeartbeat(id: string): Promise<{ ok: boolean; turns: number }> {
  const res = await fetch(`${BASE}/heartbeat/${encodeURIComponent(id)}/trigger`, {
    method: "POST",
  });
  return jsonOrThrow(res, "Heartbeat trigger error");
}

export async function reloadHeartbeats(): Promise<{ ok: boolean; heartbeats: number }> {
  const res = await fetch(`${BASE}/heartbeat/reload`, { method: "POST" });
  return jsonOrThrow(res, "Heartbeat reload error");
}

export async function listHeartbeatEvents(): Promise<{ events: HeartbeatEvent[]; count: number }> {
  const res = await fetch(`${BASE}/heartbeat/events`);
  return jsonOrThrow(res, "Heartbeat events error");
}

export async function getHeartbeatLog(
  id: string,
  limit = 50,
  offset = 0,
): Promise<{ entries: FireLogEntry[]; count: number }> {
  const res = await fetch(
    `${BASE}/heartbeat/${encodeURIComponent(id)}/log?limit=${limit}&offset=${offset}`,
  );
  return jsonOrThrow(res, "Heartbeat log error");
}
