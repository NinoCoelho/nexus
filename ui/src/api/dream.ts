import { BASE } from "./base";

export interface DreamRun {
  id: number;
  started_at: string | null;
  finished_at: string | null;
  depth: string;
  phases_run: string;
  status: string;
  tokens_in: number;
  tokens_out: number;
  duration_ms: number | null;
  memories_merged: number;
  insights_generated: number;
  skills_created: number;
  error: string | null;
}

export interface DreamStatus {
  enabled: boolean;
  running: boolean;
  last_run: DreamRun | null;
  recent_runs: DreamRun[];
  budget: {
    used_today: number;
    daily_limit: number;
  };
}

export interface DreamJournalEntry {
  date: string;
  path: string;
  size: number;
  preview: string;
}

export interface DreamSuggestion {
  name: string;
  filename: string;
  description: string;
  content: string;
}

export interface DreamTriggerResult {
  run_id: number;
  depth: string;
  phases_run: string[];
  status: string;
  duration_ms: number;
  tokens_in: number;
  tokens_out: number;
  error: string | null;
}

async function jsonOrThrow<T>(res: Response, label: string): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `${label}: ${res.status}`);
  }
  return res.json();
}

export async function getDreamStatus(): Promise<DreamStatus> {
  const res = await fetch(`${BASE}/dream/status`);
  return jsonOrThrow<DreamStatus>(res, "Dream status error");
}

export async function triggerDream(depth: string = "light"): Promise<DreamTriggerResult> {
  const res = await fetch(`${BASE}/dream/trigger?depth=${encodeURIComponent(depth)}`, {
    method: "POST",
  });
  return jsonOrThrow<DreamTriggerResult>(res, "Dream trigger error");
}

export async function listDreamJournal(): Promise<{ entries: DreamJournalEntry[] }> {
  const res = await fetch(`${BASE}/dream/journal`);
  return jsonOrThrow(res, "Dream journal list error");
}

export async function getDreamJournal(date: string): Promise<{ date: string; path: string; content: string }> {
  const res = await fetch(`${BASE}/dream/journal/${encodeURIComponent(date)}`);
  return jsonOrThrow(res, "Dream journal error");
}

export async function listDreamSuggestions(): Promise<{ suggestions: DreamSuggestion[] }> {
  const res = await fetch(`${BASE}/dream/suggestions`);
  return jsonOrThrow(res, "Dream suggestions error");
}

export async function acceptDreamSuggestion(filename: string): Promise<{ ok: boolean; skill_name: string }> {
  const res = await fetch(`${BASE}/dream/suggestions/${encodeURIComponent(filename)}/accept`, {
    method: "POST",
  });
  return jsonOrThrow(res, "Accept suggestion error");
}

export async function dismissDreamSuggestion(filename: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/dream/suggestions/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  return jsonOrThrow(res, "Dismiss suggestion error");
}

export async function listDreamRuns(limit = 50, offset = 0): Promise<{ runs: DreamRun[]; count: number }> {
  const res = await fetch(`${BASE}/dream/runs?limit=${limit}&offset=${offset}`);
  return jsonOrThrow(res, "Dream runs error");
}
