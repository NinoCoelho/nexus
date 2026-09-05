// API client for background job tracking.
import { BASE } from "./base";

export interface RunningJob {
  id: string;
  type: string;
  label: string;
  session_id: string | null;
  started_at: number;
  extra?: Record<string, unknown>;
}

export async function listRunningJobs(): Promise<RunningJob[]> {
  const res = await fetch(`${BASE}/jobs`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.jobs ?? [];
}

export async function killRunningJob(jobId: string): Promise<boolean> {
  const res = await fetch(`${BASE}/jobs/${encodeURIComponent(jobId)}/kill`, { method: "POST" });
  return res.ok;
}
