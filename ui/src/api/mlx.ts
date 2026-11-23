/**
 * API client for in-process MLX model management on Apple Silicon:
 * HF search (mlx-community), snapshot download, activate/unload, and
 * importing already-downloaded models in place (e.g. an LM Studio library).
 *
 * Backed by `agent/src/nexus/server/routes/mlx.py`. Deliberately parallel to
 * `localLlm.ts` (llama.cpp) rather than a discriminator field — MLX's unit of
 * identity is a snapshot directory, not a filename, and there is no port.
 */
import { BASE } from "./base";

export interface MlxAvailable {
  available: boolean;
  reason: string | null;
}

export interface MlxStatus {
  available: boolean;
  loaded: boolean;
  model_path?: string;
  context_window?: number;
  supports_tools?: boolean;
  tool_parser?: string;
  resident_seconds?: number;
  queue_depth?: number;
  idle_unload_seconds?: number;
}

export interface MlxHfRepo {
  id: string;
  downloads: number;
  likes: number;
  tags: string[];
}

export interface MlxFitsVerdict {
  fits: boolean | null;
  reason: string;
}

export interface MlxRepoDetails {
  repo_id: string;
  quantization_bits: number;
  context_window: number;
  is_vlm: boolean;
  architectures: string[];
  model_type: string;
  size_bytes: number;
  size_gb: number;
  estimated_resident_gb: number;
  fits: MlxFitsVerdict;
}

export interface MlxDownloadTask {
  task_id: string;
  repo_id: string;
  slug: string;
  total_bytes: number;
  downloaded_bytes: number;
  status: "pending" | "downloading" | "done" | "error" | "cancelled";
  error: string | null;
}

export interface MlxInstalled {
  repo_id: string;
  slug: string;
  local_dir: string;
  architectures: string[];
  model_type: string;
  context_window: number;
  quantization_bits: number;
  is_vlm: boolean;
  supports_tools: boolean;
  capabilities: string[];
  total_bytes: number;
  total_gb: number;
  is_active: boolean;
  model_id: string;
  /** Sharded checkpoint missing shard files (interrupted download). */
  incomplete?: boolean;
  /** Registered in place via a symlink into an external library. */
  imported?: boolean;
}

export interface MlxImportRow {
  slug: string | null;
  path: string;
  action: "imported" | "already" | "skipped";
  reason: string | null;
}

export interface MlxImportResult {
  imported: MlxImportRow[];
  already: MlxImportRow[];
  skipped: MlxImportRow[];
}

export interface MlxActivateResult {
  model_id: string;
  slug: string;
  supports_tools: boolean;
}

/** Pull the human-readable `detail` out of a FastAPI error response. */
async function errDetail(res: Response, fallback: string): Promise<Error> {
  try {
    const body = await res.json();
    return new Error(body.detail || body.error || JSON.stringify(body));
  } catch {
    return new Error(`${fallback}: ${res.status}`);
  }
}

export async function getMlxAvailable(): Promise<MlxAvailable> {
  const res = await fetch(`${BASE}/local/mlx/available`);
  if (!res.ok) throw new Error(`MLX availability error: ${res.status}`);
  return res.json();
}

export async function getMlxStatus(): Promise<MlxStatus> {
  const res = await fetch(`${BASE}/local/mlx/status`);
  if (!res.ok) throw new Error(`MLX status error: ${res.status}`);
  return res.json();
}

export async function unloadMlx(): Promise<void> {
  const res = await fetch(`${BASE}/local/mlx/unload`, { method: "POST" });
  if (!res.ok) throw new Error("Unload failed");
}

export async function searchMlxHf(q: string, limit = 20): Promise<MlxHfRepo[]> {
  const res = await fetch(`${BASE}/local/mlx/hf/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!res.ok) throw new Error(`HF search error: ${res.status}`);
  return res.json();
}

export async function mlxRepoDetails(repoId: string): Promise<MlxRepoDetails> {
  const [owner, repo] = repoId.split("/");
  if (!owner || !repo) throw new Error(`Bad repo id: ${repoId}`);
  const res = await fetch(
    `${BASE}/local/mlx/hf/repo/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
  );
  if (!res.ok) throw new Error(`Repo details error: ${res.status}`);
  return res.json();
}

export async function startMlxDownload(repoId: string): Promise<{ task_id: string; slug: string }> {
  const res = await fetch(`${BASE}/local/mlx/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_id: repoId }),
  });
  if (!res.ok) throw await errDetail(res, "Download start error");
  return res.json();
}

export async function listMlxDownloads(): Promise<MlxDownloadTask[]> {
  const res = await fetch(`${BASE}/local/mlx/downloads`);
  if (!res.ok) throw new Error(`Downloads list error: ${res.status}`);
  return res.json();
}

export async function cancelMlxDownload(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/local/mlx/download/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Cancel download error");
}

export async function listMlxInstalled(): Promise<MlxInstalled[]> {
  const res = await fetch(`${BASE}/local/mlx/installed`);
  if (!res.ok) throw new Error(`Installed list error: ${res.status}`);
  return res.json();
}

export async function activateMlx(
  slug: string,
  opts: { setDefault?: boolean | null; force?: boolean } = {},
): Promise<MlxActivateResult> {
  const res = await fetch(`${BASE}/local/mlx/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      slug,
      set_default: opts.setDefault ?? null,
      force: opts.force ?? false,
    }),
  });
  if (!res.ok) throw await errDetail(res, "Activate error");
  return res.json();
}

export async function deactivateMlx(slug: string): Promise<void> {
  const res = await fetch(`${BASE}/local/mlx/deactivate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug }),
  });
  if (!res.ok) throw await errDetail(res, "Deactivate error");
}

export async function deleteMlxInstalled(slug: string): Promise<void> {
  const res = await fetch(`${BASE}/local/mlx/installed/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await errDetail(res, "Delete error");
}

export async function importMlxFolder(path: string): Promise<MlxImportResult> {
  const res = await fetch(`${BASE}/local/mlx/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw await errDetail(res, "Import error");
  return res.json();
}

/** Mirror of the server's `slug_for`: repo id → snapshot slug. */
export function mlxSlugFor(repoId: string): string {
  const tail = (repoId || "").split("/").pop() ?? "";
  return tail
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9.]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
