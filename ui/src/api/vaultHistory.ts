// API client for the opt-in vault history (git-backed) — see vault_history.py.
import { BASE } from "./base";

export type VaultHistoryAction = "write" | "delete" | "move" | "undo" | "enable" | "other";

export interface VaultHistoryCommit {
  sha: string;
  /** Unix seconds. */
  timestamp: number;
  message: string;
  action: VaultHistoryAction;
}

export interface VaultHistoryStatus {
  enabled: boolean;
  repo_exists: boolean;
  git_available: boolean;
  commit_count: number;
  last_commit: VaultHistoryCommit | null;
}

export interface VaultUndoResult {
  undone: boolean;
  /** Set when ``undone`` is false: e.g. "no_history", "disabled", "no_repo". */
  reason: string | null;
  commit: string | null;
  restored_from: string | null;
  paths: string[];
}

export async function getVaultHistoryStatus(): Promise<VaultHistoryStatus> {
  const res = await fetch(`${BASE}/vault/history/status`);
  if (!res.ok) throw new Error(`Vault history status error: ${res.status}`);
  return res.json() as Promise<VaultHistoryStatus>;
}

export async function enableVaultHistory(): Promise<VaultHistoryStatus> {
  const res = await fetch(`${BASE}/vault/history/enable`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Vault history enable error: ${res.status} ${detail}`);
  }
  return res.json() as Promise<VaultHistoryStatus>;
}

export async function disableVaultHistory(): Promise<VaultHistoryStatus> {
  const res = await fetch(`${BASE}/vault/history/disable`, { method: "POST" });
  if (!res.ok) throw new Error(`Vault history disable error: ${res.status}`);
  return res.json() as Promise<VaultHistoryStatus>;
}

export async function getVaultHistory(
  path?: string,
  limit = 100,
): Promise<VaultHistoryCommit[]> {
  const qs = new URLSearchParams();
  if (path) qs.set("path", path);
  qs.set("limit", String(limit));
  const res = await fetch(`${BASE}/vault/history?${qs.toString()}`);
  if (!res.ok) throw new Error(`Vault history log error: ${res.status}`);
  const data = await res.json() as { commits: VaultHistoryCommit[] };
  return data.commits;
}

export async function undoVaultPath(path: string): Promise<VaultUndoResult> {
  const res = await fetch(`${BASE}/vault/history/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`Vault undo error: ${res.status}`);
  return res.json() as Promise<VaultUndoResult>;
}

export async function purgeVaultHistory(): Promise<{ ok: boolean; reason?: string }> {
  const res = await fetch(`${BASE}/vault/history/purge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`Vault history purge error: ${res.status}`);
  return res.json() as Promise<{ ok: boolean; reason?: string }>;
}
