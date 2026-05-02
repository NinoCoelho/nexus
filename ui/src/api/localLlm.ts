/**
 * API client for local LLM management: hardware probe, HF Hub search,
 * GGUF download/install/activate, and runtime llama-server control.
 *
 * Backed by `agent/src/nexus/server/routes/local_llm.py`. Download progress
 * events arrive via the existing event bus (subscribe through `subscribeVaultEvents`
 * is not used here; local LLM uses its own `/local/events` SSE stream).
 */
import { BASE } from "./base";

export interface HardwareProbe {
  ram_gb: number;
  free_disk_gb: number;
  vram_gb: number;
  chip: string;
  is_apple_silicon: boolean;
  recommended_max_params_b: number;
}

export interface HfRepo {
  id: string;
  downloads: number;
  likes: number;
  tags: string[];
}

export interface HfFile {
  filename: string;
  size_bytes: number;
  quant_label: string;
  fits_in_ram: boolean;
}

export interface DownloadTask {
  task_id: string;
  repo_id: string;
  filename: string;
  total_bytes: number;
  downloaded_bytes: number;
  status: "pending" | "downloading" | "done" | "error";
  error: string | null;
}

export interface InstalledModel {
  filename: string;
  size_bytes: number;
  is_running: boolean;
  /** @deprecated alias for is_running, kept for older builds */
  is_active: boolean;
  port: number | null;
  slug: string;
  /** True for vision-language projector sidecars (`*mmproj*.gguf`) — listed
   *  so the UI can wait for them before auto-starting the language model,
   *  but hidden from the user-facing tile list. */
  is_mmproj?: boolean;
}

export async function getHardware(): Promise<HardwareProbe> {
  const res = await fetch(`${BASE}/local/hardware`);
  if (!res.ok) throw new Error(`Hardware probe error: ${res.status}`);
  return res.json();
}

export async function searchHf(q: string, limit = 20): Promise<HfRepo[]> {
  const res = await fetch(`${BASE}/local/hf/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!res.ok) throw new Error(`HF search error: ${res.status}`);
  return res.json();
}

export async function listRepoFiles(repoId: string): Promise<HfFile[]> {
  const [owner, repo] = repoId.split("/");
  if (!owner || !repo) throw new Error(`Bad repo id: ${repoId}`);
  const res = await fetch(
    `${BASE}/local/hf/repo/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/files`,
  );
  if (!res.ok) throw new Error(`HF repo files error: ${res.status}`);
  return res.json();
}

export async function startDownload(repoId: string, filename: string): Promise<{ task_id: string }> {
  const res = await fetch(`${BASE}/local/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_id: repoId, filename }),
  });
  if (!res.ok) throw new Error(`Download start error: ${res.status}`);
  return res.json();
}

export async function listDownloads(): Promise<DownloadTask[]> {
  const res = await fetch(`${BASE}/local/downloads`);
  if (!res.ok) throw new Error(`Downloads list error: ${res.status}`);
  return res.json();
}

export async function listInstalled(): Promise<InstalledModel[]> {
  const res = await fetch(`${BASE}/local/installed`);
  if (!res.ok) throw new Error(`Installed list error: ${res.status}`);
  return res.json();
}

export async function startModel(filename: string): Promise<void> {
  const res = await fetch(`${BASE}/local/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Start error: ${res.status} ${detail}`);
  }
}

export async function stopModel(filename: string): Promise<void> {
  const res = await fetch(`${BASE}/local/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Stop error: ${res.status} ${detail}`);
  }
}

/** @deprecated use startModel */
export const activateModel = startModel;

export async function deleteInstalled(filename: string): Promise<void> {
  const res = await fetch(`${BASE}/local/installed/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Delete error: ${res.status} ${detail}`);
  }
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
