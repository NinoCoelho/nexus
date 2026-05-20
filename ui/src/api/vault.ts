// API client for vault files, tree, search, mentions, backlinks, tags, move, upload.
import { BASE } from "./base";

export interface VaultNode {
  path: string;
  type: "file" | "dir";
  size?: number;
  /** Unix seconds (float). Backend emits `stat.st_mtime`. */
  mtime?: number;
}

export interface VaultTagCount { tag: string; count: number }

export interface VaultFile {
  path: string;
  content: string;
  frontmatter?: Record<string, unknown>;
  body?: string;
  tags?: string[];
  backlinks?: string[];
  /** File size in bytes. */
  size?: number;
  /** Unix seconds (float). */
  mtime?: number;
  /** True if the file could not be decoded as UTF-8 text. */
  binary?: boolean;
}

export interface VaultSearchResult { path: string; snippet: string; score: number }

export interface VaultUploadResult {
  uploaded: { path: string; size: number }[];
}

/** URL that streams raw bytes of a vault file with a guessed Content-Type. */
export function vaultRawUrl(path: string): string {
  return `${BASE}/vault/raw?path=${encodeURIComponent(path)}`;
}

/** Lazy server-side transcription of a vault audio file. Cached on the
 * server by (path, mtime), so this is cheap to call repeatedly. */
export async function transcribeVaultAudio(path: string): Promise<{ text: string }> {
  const res = await fetch(`${BASE}/vault/transcribe?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    let detail = `${res.status}`;
    try { const j = await res.json(); if (j?.detail) detail = j.detail; } catch { /* ignore */ }
    throw new Error(`Transcription failed: ${detail}`);
  }
  return res.json();
}

export async function getVaultTree(): Promise<VaultNode[]> {
  const res = await fetch(`${BASE}/vault/tree`);
  if (!res.ok) throw new Error(`Vault tree error: ${res.status}`);
  return res.json();
}

export async function uploadVaultFiles(
  files: File[],
  destDir?: string,
): Promise<VaultUploadResult> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (destDir) form.append("path", destDir);
  const res = await fetch(`${BASE}/vault/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Vault upload error: ${res.status}`);
  return res.json();
}

export async function getVaultFile(path: string): Promise<VaultFile> {
  const res = await fetch(`${BASE}/vault/file?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Vault file error: ${res.status}`);
  return res.json();
}

export async function putVaultFile(path: string, content: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/file`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  if (!res.ok) throw new Error(`Vault put error: ${res.status}`);
}

export async function deleteVaultFile(path: string, recursive = false): Promise<void> {
  const url = `${BASE}/vault/file?path=${encodeURIComponent(path)}${recursive ? "&recursive=true" : ""}`;
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) throw new Error(`Vault delete error: ${res.status}`);
}

export async function postVaultFolder(path: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`Vault folder error: ${res.status}`);
}

export async function searchVaultMentions(q: string, limit = 8): Promise<VaultNode[]> {
  const res = await fetch(`${BASE}/vault/mention?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!res.ok) throw new Error(`Vault mention error: ${res.status}`);
  const data = await res.json() as { results: VaultNode[] };
  return data.results;
}

export async function searchVault(q: string, limit = 50): Promise<VaultSearchResult[]> {
  const res = await fetch(`${BASE}/vault/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!res.ok) throw new Error(`Vault search error: ${res.status}`);
  const data = await res.json() as { results: VaultSearchResult[] };
  return data.results;
}

export async function reindexVault(full = false): Promise<{ indexed: number; full?: boolean }> {
  const url = `${BASE}/vault/reindex${full ? "?full=1" : ""}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) throw new Error(`Vault reindex error: ${res.status}`);
  return res.json() as Promise<{ indexed: number; full?: boolean }>;
}

export async function postVaultMove(from: string, to: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from, to }),
  });
  if (!res.ok) throw new Error(`Vault move error: ${res.status}`);
}

export async function getVaultTags(): Promise<VaultTagCount[]> {
  const res = await fetch(`${BASE}/vault/tags`);
  if (!res.ok) throw new Error(`Vault tags error: ${res.status}`);
  return res.json() as Promise<VaultTagCount[]>;
}

export async function getVaultTag(tag: string): Promise<{ tag: string; files: string[] }> {
  const res = await fetch(`${BASE}/vault/tags/${encodeURIComponent(tag)}`);
  if (!res.ok) throw new Error(`Vault tag error: ${res.status}`);
  return res.json() as Promise<{ tag: string; files: string[] }>;
}

export async function getVaultBacklinks(path: string): Promise<{ path: string; backlinks: string[] }> {
  const res = await fetch(`${BASE}/vault/backlinks?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Vault backlinks error: ${res.status}`);
  return res.json() as Promise<{ path: string; backlinks: string[] }>;
}

export async function getVaultForwardLinks(path: string): Promise<{ path: string; forward_links: string[] }> {
  const res = await fetch(`${BASE}/vault/forward-links?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Vault forward links error: ${res.status}`);
  return res.json() as Promise<{ path: string; forward_links: string[] }>;
}

export function vaultExportPdfUrl(path: string): string {
  return `${BASE}/vault/export/pdf?path=${encodeURIComponent(path)}`;
}

// ── Import wizard types and API ──────────────────────────────────────────────

export interface ImportTreeNode {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
  children?: ImportTreeNode[];
}

export interface ImportCsvInfo {
  path: string;
  name: string;
  headers: string[];
  column_count: number;
  estimated_rows: number;
  size: number;
  mode?: "as_is" | "app";
}

export interface ImportStats {
  total_files: number;
  total_size: number;
  csvs: ImportCsvInfo[];
}

export interface ZipPreviewResult {
  import_id: string;
  tree: ImportTreeNode[];
  stats: ImportStats;
  export_format?: { format: string; conversation_count: number } | null;
}

export interface ImportOptions {
  selected_paths: string[];
  dest_dir: string;
  csv_options: Record<string, "as_is" | "app">;
  process_options?: { prompt: string; keep_originals: boolean };
  export_options?: { format: string; import_as: "conversations" | "raw" };
}

export interface CsvProposal {
  entities: Array<{
    name: string;
    fields: Array<{ name: string; kind: string; choices?: string[]; required?: boolean }>;
    sample_values?: Record<string, unknown[]>;
  }>;
  relationships: Array<{
    from: string;
    to: string;
    type: string;
    via_field: string;
    description?: string;
  }>;
}

export type ImportSseEvent =
  | { event: "file_start"; data: { path: string; action: string; entity?: string } }
  | { event: "file_done"; data: { path: string; action: string; size?: number; entity?: string; added?: number; skipped?: number } }
  | { event: "file_error"; data: { path: string; error: string; entity?: string } }
  | { event: "done"; data: { stats: { imported: number; processed: number; errors: number }; csv_apps?: string[]; batch_id?: string } };

export async function uploadZipPreview(file: File): Promise<ZipPreviewResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/vault/upload/zip-preview`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = `Upload failed: ${res.status}`;
    try { const j = await res.json(); if (j?.detail) detail = j.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function cancelZipImport(importId: string): Promise<void> {
  await fetch(`${BASE}/vault/import/zip/${importId}`, { method: "DELETE" });
}

function parseSseLines(text: string): ImportSseEvent[] {
  const events: ImportSseEvent[] = [];
  let currentEvent = "";
  let currentData = "";
  for (const line of text.split("\n")) {
    if (line.startsWith("event: ")) {
      currentEvent = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      currentData = line.slice(6);
    } else if (line === "" && currentEvent && currentData) {
      try {
        const data = JSON.parse(currentData);
        events.push({ event: currentEvent as ImportSseEvent["event"], data } as ImportSseEvent);
      } catch { /* skip malformed */ }
      currentEvent = "";
      currentData = "";
    }
  }
  return events;
}

export async function streamZipImport(
  importId: string,
  options: ImportOptions,
  onEvent: (event: ImportSseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/vault/import/zip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ import_id: importId, ...options }),
    signal,
  });
  if (!res.ok) throw new Error(`Import failed: ${res.status}`);
  await _consumeSse(res, onEvent);
}

export async function streamBatchImport(
  files: Map<string, File>,
  options: ImportOptions,
  onEvent: (event: ImportSseEvent) => void,
  signal?: AbortSignal,
): Promise<string | undefined> {
  const form = new FormData();
  form.append("options", JSON.stringify(options));
  let batchId: string | undefined;
  for (const [path, file] of files) {
    form.append("files", file, path);
  }
  const res = await fetch(`${BASE}/vault/import/batch`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!res.ok) throw new Error(`Import failed: ${res.status}`);
  await _consumeSse(res, (ev) => {
    if (ev.event === "done" && "batch_id" in ev.data) {
      batchId = ev.data.batch_id as string;
    }
    onEvent(ev);
  });
  return batchId;
}

export async function analyzeCsv(params: {
  csv_path: string;
  import_id?: string;
  batch_id?: string;
  source?: "temp" | "vault";
}): Promise<{ proposal: CsvProposal; csv_stats: { rows: number; columns: number; headers: string[] } }> {
  const res = await fetch(`${BASE}/vault/csv-analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...params, source: params.source ?? (params.import_id ? "temp" : "vault") }),
  });
  if (!res.ok) {
    let detail = `CSV analysis failed: ${res.status}`;
    try { const j = await res.json(); if (j?.detail) detail = j.detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function streamCsvMigrate(
  params: {
    csv_path: string;
    import_id?: string;
    batch_id?: string;
    source?: "temp" | "vault";
    dest_dir: string;
    approved_plan: CsvProposal;
  },
  onEvent: (event: ImportSseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/vault/csv-migrate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...params, source: params.source ?? (params.import_id ? "temp" : "vault") }),
    signal,
  });
  if (!res.ok) throw new Error(`CSV migration failed: ${res.status}`);
  await _consumeSse(res, onEvent);
}

async function _consumeSse(
  res: Response,
  onEvent: (event: ImportSseEvent) => void,
): Promise<void> {
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      if (!part.trim()) continue;
      const events = parseSseLines(part + "\n\n");
      for (const ev of events) onEvent(ev);
    }
  }
  if (buffer.trim()) {
    const events = parseSseLines(buffer + "\n\n");
    for (const ev of events) onEvent(ev);
  }
}
