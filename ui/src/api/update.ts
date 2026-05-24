import { BASE } from "./base";

export interface UpdateAsset {
  name: string;
  browser_download_url: string;
  size: number;
}

export interface UpdateCheckResult {
  current: string;
  latest: string;
  update_available: boolean;
  skipped: boolean;
  html_url: string;
  body: string;
  assets: UpdateAsset[];
}

export interface UpdateStatus {
  state: "idle" | "downloading" | "ready" | "installing" | "error";
  file?: string;
  tag?: string;
  error?: string;
  progress?: number;
}

export async function checkUpdate(): Promise<UpdateCheckResult> {
  const res = await fetch(`${BASE}/update/check`);
  if (!res.ok) throw new Error(`update check error: ${res.status}`);
  return res.json();
}

export async function downloadUpdate(): Promise<void> {
  const res = await fetch(`${BASE}/update/download`, { method: "POST" });
  if (!res.ok) throw new Error(`update download error: ${res.status}`);
}

export async function getUpdateStatus(): Promise<UpdateStatus> {
  const res = await fetch(`${BASE}/update/status`);
  if (!res.ok) throw new Error(`update status error: ${res.status}`);
  return res.json();
}

export async function installUpdate(): Promise<{ status: string; message: string }> {
  const res = await fetch(`${BASE}/update/install`, { method: "POST" });
  if (!res.ok) throw new Error(`update install error: ${res.status}`);
  return res.json();
}

export async function skipVersion(version: string): Promise<void> {
  const res = await fetch(`${BASE}/update/skip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) throw new Error(`skip version error: ${res.status}`);
}
