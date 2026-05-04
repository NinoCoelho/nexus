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
