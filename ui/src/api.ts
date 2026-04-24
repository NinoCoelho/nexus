const BASE = import.meta.env.VITE_NEXUS_API ?? "http://localhost:18989";

// ── Sessions ──────────────────────────────────────────────────────────────────

export interface SessionSummary {
  id: string;
  title: string;
  // Backend sends unix seconds as integers. Declared as number|string to keep
  // callers defensive against an older backend that emitted ISO strings.
  created_at: number | string;
  updated_at: number | string;
  message_count: number;
}

export interface SessionMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: unknown;
  tool_call_id?: string;
  created_at: string;
}

export interface SessionDetail {
  id: string;
  title: string;
  context?: string;
  messages: SessionMessage[];
}

/**
 * Ping the backend /health endpoint. Resolves true on a fast 200, false on
 * any failure (connection refused, 5xx, timeout). Used by the reachability
 * pill in the UI.
 */
export async function pingHealth(timeoutMs = 3000): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}/health`, { signal: ctrl.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function getSessions(limit = 50): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE}/sessions?limit=${limit}`);
  if (!res.ok) throw new Error(`Sessions error: ${res.status}`);
  return res.json();
}

export interface SessionSearchResult {
  session_id: string;
  title: string;
  snippet: string;
}

export async function searchSessions(
  q: string,
  limit = 20,
): Promise<SessionSearchResult[]> {
  if (!q.trim()) return [];
  const res = await fetch(
    `${BASE}/sessions/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );
  if (!res.ok) throw new Error(`Search error: ${res.status}`);
  return res.json();
}

export async function getSession(id: string): Promise<SessionDetail> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Session error: ${res.status}`);
  return res.json();
}

export interface SessionToVaultResult {
  mode: "raw" | "summary";
  path: string;
  bytes?: number;
  length?: number;
}

export async function sessionToVault(
  id: string,
  mode: "raw" | "summary",
  path?: string,
): Promise<SessionToVaultResult> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}/to-vault`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, ...(path ? { path } : {}) }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `to-vault error: ${res.status}`);
  }
  return res.json();
}

export async function patchSession(id: string, patch: { title?: string }): Promise<SessionDetail> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Session patch error: ${res.status}`);
  return res.json();
}

export async function truncateSession(sessionId: string, beforeSeq: number): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ before_seq: beforeSeq }),
  });
  if (!res.ok) throw new Error(`Truncate error: ${res.status}`);
}

// ── Vault ─────────────────────────────────────────────────────────────────────

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

/** URL that streams raw bytes of a vault file with a guessed Content-Type. */
export function vaultRawUrl(path: string): string {
  return `${BASE}/vault/raw?path=${encodeURIComponent(path)}`;
}

export async function getVaultTree(): Promise<VaultNode[]> {
  const res = await fetch(`${BASE}/vault/tree`);
  if (!res.ok) throw new Error(`Vault tree error: ${res.status}`);
  return res.json();
}

export interface VaultUploadResult {
  uploaded: { path: string; size: number }[];
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

export async function transcribeAudio(blob: Blob, language?: string): Promise<{ text: string }> {
  const form = new FormData();
  const name = blob.type.includes("webm") ? "audio.webm" : "audio.bin";
  form.append("file", blob, name);
  if (language) form.append("language", language);
  const res = await fetch(`${BASE}/transcribe`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch { /* ignore */ }
    throw new Error(`Transcription failed: ${detail}`);
  }
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

export interface VaultSearchResult { path: string; snippet: string; score: number }

export async function searchVault(q: string, limit = 50): Promise<VaultSearchResult[]> {
  const res = await fetch(`${BASE}/vault/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!res.ok) throw new Error(`Vault search error: ${res.status}`);
  const data = await res.json() as { results: VaultSearchResult[] };
  return data.results;
}

export async function reindexVault(): Promise<{ indexed: number }> {
  const res = await fetch(`${BASE}/vault/reindex`, { method: "POST" });
  if (!res.ok) throw new Error(`Vault reindex error: ${res.status}`);
  return res.json() as Promise<{ indexed: number }>;
}

export async function postVaultMove(from: string, to: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from, to }),
  });
  if (!res.ok) throw new Error(`Vault move error: ${res.status}`);
}

export interface GraphNode { path: string; size: number; folder: string; tags: string[]; title: string }
export interface GraphEdge { from: string; to: string; type?: string }
export interface EntityNode { id: number; name: string; type: string; source_paths: string[] }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[]; orphans: string[]; entity_nodes?: EntityNode[] }

export interface GraphScopeParams {
  scope?: string;
  seed?: string;
  hops?: number;
  edge_types?: string;
}

export async function getVaultGraph(params?: GraphScopeParams): Promise<GraphData> {
  const qs = new URLSearchParams();
  if (params?.scope && params.scope !== "all") qs.set("scope", params.scope);
  if (params?.seed) qs.set("seed", params.seed);
  if (params?.hops) qs.set("hops", String(params.hops));
  if (params?.edge_types) qs.set("edge_types", params.edge_types);
  const query = qs.toString();
  const url = query ? `${BASE}/vault/graph?${query}` : `${BASE}/vault/graph`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Vault graph error: ${res.status}`);
  return res.json() as Promise<GraphData>;
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

export async function getVaultEntitySources(path: string): Promise<{ path: string; entities: { id: number; name: string; type: string }[] }> {
  const res = await fetch(`${BASE}/vault/graph/entity-sources?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Vault entity sources error: ${res.status}`);
  return res.json();
}

// ── Vault-native Kanban ──────────────────────────────────────────────────────

export type KanbanCardStatus = "running" | "done" | "failed";

export interface KanbanCard {
  id: string;
  title: string;
  body?: string;
  session_id?: string;
  status?: KanbanCardStatus;
}

export interface KanbanLane {
  id: string;
  title: string;
  cards: KanbanCard[];
  prompt?: string;
}

export interface KanbanBoard {
  path: string;
  title: string;
  lanes: KanbanLane[];
}

export async function getVaultKanban(path: string): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/vault/kanban?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Kanban load error: ${res.status}`);
  return res.json();
}

export async function createVaultKanban(
  path: string,
  opts: { title?: string; columns?: string[] } = {},
): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/vault/kanban`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, ...opts }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Kanban create error: ${res.status}`);
  }
  return res.json();
}

export async function addVaultKanbanCard(
  path: string,
  card: { lane: string; title: string; body?: string },
): Promise<KanbanCard> {
  const res = await fetch(`${BASE}/vault/kanban/cards?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(card),
  });
  if (!res.ok) throw new Error(`Kanban card create error: ${res.status}`);
  return res.json();
}

export async function patchVaultKanbanCard(
  path: string,
  cardId: string,
  patch: { title?: string; body?: string; lane?: string; position?: number; session_id?: string | null },
): Promise<KanbanCard> {
  const res = await fetch(`${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}?path=${encodeURIComponent(path)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Kanban card patch error: ${res.status}`);
  return res.json();
}

export async function deleteVaultKanbanCard(path: string, cardId: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban card delete error: ${res.status}`);
}

export async function addVaultKanbanLane(path: string, title: string): Promise<KanbanLane> {
  const res = await fetch(`${BASE}/vault/kanban/lanes?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Kanban lane create error: ${res.status}`);
  return res.json();
}

export async function deleteVaultKanbanLane(path: string, laneId: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban lane delete error: ${res.status}`);
}

export async function patchVaultKanbanLane(
  path: string,
  laneId: string,
  patch: { title?: string; prompt?: string | null },
): Promise<KanbanLane> {
  const res = await fetch(
    `${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}?path=${encodeURIComponent(path)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  if (!res.ok) throw new Error(`Kanban lane patch error: ${res.status}`);
  return res.json();
}

// ── Dispatch (start chat seeded from a vault file or kanban card) ────────────

export type DispatchMode = "chat" | "background" | "chat-hidden";

export interface DispatchResult {
  session_id: string;
  /** Seed text the caller should feed into /chat/stream. Absent for `background` mode. */
  seed_message?: string;
  path: string;
  card_id?: string | null;
  mode?: DispatchMode;
}

/**
 * Marker prefix the server uses on seeded user messages that should NOT
 * render as chat bubbles. The chat view filters messages whose content
 * starts with this string.
 */
export const HIDDEN_SEED_MARKER = "<!-- nx:hidden-seed -->\n";

export async function dispatchFromVault(
  body: { path: string; card_id?: string; mode?: DispatchMode },
): Promise<DispatchResult> {
  const res = await fetch(`${BASE}/vault/dispatch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Dispatch error: ${res.status}`);
  return res.json();
}

export interface TraceEvent {
  iter: number;
  tool?: string;
  args?: unknown;
  result?: unknown;
  status?: "pending" | "done" | "error";
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  trace: TraceEvent[];
  skills_touched: string[];
}

export interface SkillSummary {
  name: string;
  description: string;
  trust: "builtin" | "user" | "agent";
}

export interface SkillDetail {
  name: string;
  body: string;
  frontmatter: Record<string, unknown>;
}

export type StreamEvent =
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; args?: unknown; result_preview?: string }
  | { type: "done"; session_id: string; reply: string; trace: TraceEvent[]; skills_touched: string[]; model?: string; routed_by?: "user" | "auto" }
  | { type: "limit_reached"; iterations: number }
  | { type: "error"; detail: string; reason?: string; retryable?: boolean; status_code?: number | null };

export async function chatStream(
  message: string,
  session_id: string | undefined,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
  model?: string,
  routing_mode?: "fixed" | "auto",
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id, model, routing_mode }),
    signal,
  });

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch { /* ignore */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE frames are separated by \n\n
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";

    for (const frame of frames) {
      if (!frame.trim()) continue;
      let eventName = "message";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLine = line.slice(5).trim();
        }
      }
      if (!dataLine) continue;
      try {
        const parsed = JSON.parse(dataLine) as Record<string, unknown>;
        if (eventName === "delta") {
          onEvent({ type: "delta", text: parsed.text as string });
        } else if (eventName === "tool") {
          onEvent({
            type: "tool",
            name: parsed.name as string,
            args: parsed.args,
            result_preview: parsed.result_preview as string | undefined,
          });
        } else if (eventName === "done") {
          const usage = parsed.usage as Record<string, unknown> | undefined;
          onEvent({
            type: "done",
            session_id: parsed.session_id as string,
            reply: parsed.reply as string,
            trace: (parsed.trace ?? []) as TraceEvent[],
            skills_touched: (parsed.skills_touched ?? []) as string[],
            model: (usage?.model ?? parsed.model) as string | undefined,
            routed_by: parsed.routed_by as "user" | "auto" | undefined,
          });
        } else if (eventName === "limit_reached") {
          onEvent({ type: "limit_reached", iterations: (parsed.iterations as number) ?? 0 });
        } else if (eventName === "error") {
          onEvent({
            type: "error",
            detail: parsed.detail as string,
            reason: parsed.reason as string | undefined,
            retryable: parsed.retryable as boolean | undefined,
            status_code: parsed.status_code as number | null | undefined,
          });
        }
      } catch { /* malformed frame — skip */ }
    }
  }
}

export async function postChat(
  message: string,
  session_id?: string,
  context?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id, context }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      // body was not JSON; keep the status
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getSkills(): Promise<SkillSummary[]> {
  const res = await fetch(`${BASE}/skills`);
  if (!res.ok) throw new Error(`Skills error: ${res.status}`);
  return res.json();
}

export async function getSkill(name: string): Promise<SkillDetail> {
  const res = await fetch(`${BASE}/skills/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Skill error: ${res.status}`);
  return res.json();
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function exportSession(id: string): Promise<{ markdown: string; filename: string }> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}/export`);
  if (!res.ok) throw new Error(`Export error: ${res.status}`);
  const markdown = await res.text();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : `session-${id.slice(0, 8)}.md`;
  return { markdown, filename };
}

export async function importSession(markdown: string): Promise<{ id: string; title: string; imported_message_count: number }> {
  const res = await fetch(`${BASE}/sessions/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown }),
  });
  if (!res.ok) throw new Error(`Import error: ${res.status}`);
  return res.json();
}

export async function getHealth(): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

// ── Settings types ────────────────────────────────────────────────────────────

export type ModelTier = "fast" | "balanced" | "heavy";

export interface Model {
  id: string;
  provider: string;
  model_name: string;
  tags: string[];
  tier: ModelTier;
  notes: string;
}

export interface Provider {
  name: string;
  base_url?: string;
  has_key: boolean;
  key_env?: string;
  key_source: "inline" | "env" | "anonymous" | null;
  type?: "openai_compat" | "anthropic" | "ollama";
}

export interface AgentConfig {
  default_model: string;
  routing_mode: "fixed" | "auto";
  max_iterations: number;
}

export interface TranscriptionConfig {
  mode: "local" | "remote";
  model: string;
  language: string;
  device: "cpu" | "cuda" | "auto";
  compute_type: string;
  remote: {
    base_url: string;
    api_key_env: string;
    model: string;
  };
}

export interface Config {
  agent: AgentConfig;
  providers: Record<string, { base_url?: string; key_env?: string; has_key: boolean }>;
  models: Model[];
  transcription?: TranscriptionConfig;
}

export interface RoutingConfig {
  default_model: string;
  last_used_model: string;
  classification_model: string;
  routing_mode: "fixed" | "auto";
  available_models: string[];
  embedding_model_id: string;
  extraction_model_id: string;
}

// ── Settings API calls ────────────────────────────────────────────────────────

export async function getConfig(): Promise<Config> {
  const res = await fetch(`${BASE}/config`);
  if (!res.ok) throw new Error(`Config error: ${res.status}`);
  return res.json();
}

export async function patchConfig(patch: Partial<Config>): Promise<Config> {
  const res = await fetch(`${BASE}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Config patch error: ${res.status}`);
  return res.json();
}

export async function getProviders(): Promise<Provider[]> {
  const res = await fetch(`${BASE}/providers`);
  if (!res.ok) throw new Error(`Providers error: ${res.status}`);
  return res.json();
}

export async function fetchProviderModels(name: string): Promise<{ models: string[]; ok: boolean; error: string | null }> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}/models`);
  if (!res.ok) throw new Error(`Provider models error: ${res.status}`);
  return res.json();
}

export async function setProviderKey(name: string, apiKey: string): Promise<void> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}/key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) throw new Error(`Set key error: ${res.status}`);
}

export async function clearProviderKey(name: string): Promise<void> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}/key`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Clear key error: ${res.status}`);
}

export async function getModels(): Promise<Model[]> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) throw new Error(`Models error: ${res.status}`);
  return res.json();
}

export async function postModel(model: Model): Promise<Model> {
  const res = await fetch(`${BASE}/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(model),
  });
  if (!res.ok) throw new Error(`Model create error: ${res.status}`);
  return res.json();
}

export async function deleteModel(id: string): Promise<void> {
  const res = await fetch(`${BASE}/models/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Model delete error: ${res.status}`);
}

export async function patchModel(
  id: string,
  patch: { model_name?: string; tags?: string[]; tier?: ModelTier; notes?: string },
): Promise<Model> {
  const res = await fetch(`${BASE}/models/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Model update error: ${res.status}`);
  return res.json();
}

export async function suggestModelTier(model_name: string): Promise<{ tier: ModelTier; source: string }> {
  const res = await fetch(`${BASE}/models/suggest-tier`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_name }),
  });
  if (!res.ok) throw new Error(`Suggest tier error: ${res.status}`);
  return res.json();
}

export async function getRouting(): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`);
  if (!res.ok) throw new Error(`Routing error: ${res.status}`);
  return res.json();
}

export async function putRouting(patch: {
  default_model?: string;
  last_used_model?: string;
  classification_model?: string;
  routing_mode?: "fixed" | "auto";
  embedding_model_id?: string;
  extraction_model_id?: string;
}): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Routing update error: ${res.status}`);
  return res.json();
}

export async function setModelRole(role: string, model_id: string): Promise<{ role: string; model_id: string }> {
  const res = await fetch(`${BASE}/models/roles`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, model_id }),
  });
  if (!res.ok) throw new Error(`Model role error: ${res.status}`);
  return res.json();
}

export async function clearModelRole(role: string): Promise<void> {
  const res = await fetch(`${BASE}/models/roles`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, model_id: "" }),
  });
  if (!res.ok) throw new Error(`Model role clear error: ${res.status}`);
}

// ── Insights ──────────────────────────────────────────────────────────────

export interface InsightsOverview {
  total_sessions: number;
  total_messages: number;
  user_messages: number;
  assistant_messages: number;
  tool_messages: number;
  avg_messages_per_session: number;
  total_active_seconds: number;
  avg_session_duration: number;
  date_range_start: number | null;
  date_range_end: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  sessions_priced: number;
  sessions_unpriced: number;
}

export interface InsightsTool { tool: string; count: number; percentage: number }

export interface InsightsActivityDay { day: string; count: number }
export interface InsightsActivityHour { hour: number; count: number }

export interface InsightsActivity {
  by_day: InsightsActivityDay[];
  by_hour: InsightsActivityHour[];
  busiest_day: InsightsActivityDay | null;
  busiest_hour: InsightsActivityHour | null;
  active_days: number;
  max_streak: number;
}

export interface InsightsTopSession {
  label: string;
  session_id: string;
  title: string;
  value: string;
  date: string;
}

export interface InsightsModel {
  model: string;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  has_pricing: boolean;
}

export interface InsightsReport {
  days: number;
  model_filter?: string | null;
  empty: boolean;
  overview: InsightsOverview;
  models: InsightsModel[];
  tools: InsightsTool[];
  activity: InsightsActivity;
  top_sessions: InsightsTopSession[];
  generated_at: number;
}

// ── Agent Graph ───────────────────────────────────────────────────────────

export interface AgentGraphNode {
  id: string;
  label: string;
  type: "agent" | "skill" | "session";
  meta: Record<string, unknown>;
}

export interface AgentGraphEdge {
  source: string;
  target: string;
  label: string;
}

export interface AgentGraphData {
  nodes: AgentGraphNode[];
  edges: AgentGraphEdge[];
}

export async function getAgentGraph(): Promise<AgentGraphData> {
  const res = await fetch(`${BASE}/graph`);
  if (!res.ok) throw new Error(`Agent graph error: ${res.status}`);
  return res.json() as Promise<AgentGraphData>;
}

// ── Knowledge Graph (GraphRAG) ──────────────────────────────────────────────

export interface KnowledgeGraphNode {
  id: string;
  name: string;
  type: string;
}

export interface KnowledgeGraphEdge {
  source: string;
  target: string;
  relation: string;
  strength: number;
}

export interface KnowledgeGraphData {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  enabled: boolean;
}

export async function getKnowledgeGraph(): Promise<KnowledgeGraphData> {
  const res = await fetch(`${BASE}/graph/knowledge`);
  if (!res.ok) throw new Error(`Knowledge graph error: ${res.status}`);
  return res.json() as Promise<KnowledgeGraphData>;
}

// ── Knowledge Dashboard ────────────────────────────────────────────────────

export interface KnowledgeEvidence {
  chunk_id: string;
  source_path: string;
  heading: string;
  content: string;
  score: number;
  source: "vector" | "graph";
  related_entities: string[];
}

export interface KnowledgeHop {
  from: string;
  to: string;
  relation: string;
  depth: number;
}

export interface KnowledgeQueryResult {
  enabled: boolean;
  results: KnowledgeEvidence[];
  trace: {
    seed_entities: string[];
    hops: KnowledgeHop[];
    expanded_entity_ids: number[];
  } | null;
  subgraph: {
    nodes: { id: number; name: string; type: string; degree?: number }[];
    edges: { source: number; target: number; relation: string; strength: number }[];
  };
}

export async function knowledgeQuery(query: string, limit = 10): Promise<KnowledgeQueryResult> {
  const res = await fetch(`${BASE}/graph/knowledge/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit }),
  });
  if (!res.ok) throw new Error(`Knowledge query error: ${res.status}`);
  return res.json() as Promise<KnowledgeQueryResult>;
}

export interface KnowledgeEntity {
  id: number;
  name: string;
  type: string;
  degree: number;
}

export async function getKnowledgeEntities(opts?: { type?: string; search?: string; limit?: number; offset?: number }): Promise<{ entities: KnowledgeEntity[]; total: number; enabled: boolean }> {
  const params = new URLSearchParams();
  if (opts?.type) params.set("type", opts.type);
  if (opts?.search) params.set("search", opts.search);
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
  const res = await fetch(`${BASE}/graph/knowledge/entities?${params}`);
  if (!res.ok) throw new Error(`Knowledge entities error: ${res.status}`);
  return res.json();
}

export interface EntityRelation {
  entity_id: number;
  entity_name: string;
  entity_type: string;
  relation: string;
  direction: "incoming" | "outgoing";
  strength: number;
}

export interface EntityDetail {
  enabled: boolean;
  entity: { id: number; name: string; type: string; description: string } | null;
  degree: number;
  relations: EntityRelation[];
  chunks: { chunk_id: string; source_path: string; heading: string }[];
}

export async function getKnowledgeEntity(id: number): Promise<EntityDetail> {
  const res = await fetch(`${BASE}/graph/knowledge/entity/${id}`);
  if (!res.ok) throw new Error(`Entity detail error: ${res.status}`);
  return res.json() as Promise<EntityDetail>;
}

export interface SubgraphData {
  enabled: boolean;
  nodes: { id: number; name: string; type: string; degree: number }[];
  edges: { source: number; target: number; relation: string; strength: number }[];
}

export async function getKnowledgeSubgraph(seed: number, hops = 2): Promise<SubgraphData> {
  const res = await fetch(`${BASE}/graph/knowledge/subgraph?seed=${seed}&hops=${hops}`);
  if (!res.ok) throw new Error(`Subgraph error: ${res.status}`);
  return res.json() as Promise<SubgraphData>;
}

export async function getKnowledgeFileSubgraph(path: string): Promise<SubgraphData> {
  const res = await fetch(`${BASE}/graph/knowledge/file-subgraph?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`File subgraph error: ${res.status}`);
  const data = await res.json();
  return { enabled: true, nodes: data.nodes ?? [], edges: data.edges ?? [] };
}

export async function getKnowledgeFolderSubgraph(folder: string): Promise<SubgraphData> {
  const res = await fetch(`${BASE}/graph/knowledge/folder-subgraph?folder=${encodeURIComponent(folder)}`);
  if (!res.ok) throw new Error(`Folder subgraph error: ${res.status}`);
  const data = await res.json();
  return { enabled: true, nodes: data.nodes ?? [], edges: data.edges ?? [] };
}

export interface GraphragIndexFileResult {
  queued?: boolean;
  enabled?: boolean;
  reason?: string;
  path?: string;
}

export async function graphragIndexFile(path: string): Promise<GraphragIndexFileResult> {
  const res = await fetch(`${BASE}/graph/knowledge/index-file`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`Index file error: ${res.status}`);
  return res.json();
}

export interface GraphragIndexStatus {
  status: "unknown" | "indexing" | "done" | "error";
  node_count?: number;
  edge_count?: number;
  detail?: string;
  nodes?: { id: number; name: string; type: string; degree?: number }[];
  edges?: { source: number; target: number; relation: string; strength: number }[];
}

export async function getGraphragIndexStatus(path: string): Promise<GraphragIndexStatus> {
  const res = await fetch(`${BASE}/graph/knowledge/index-file-status?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Index status error: ${res.status}`);
  return res.json();
}

export interface KnowledgeStats {
  enabled: boolean;
  entities: number;
  triples: number;
  types: Record<string, number>;
  components: { id: number; size: number; entities: number[] }[];
  component_count: number;
}

export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  const res = await fetch(`${BASE}/graph/knowledge/stats`);
  if (!res.ok) throw new Error(`Knowledge stats error: ${res.status}`);
  return res.json() as Promise<KnowledgeStats>;
}

// ── GraphRAG Reindex ──────────────────────────────────────────────────────

export interface ReindexFileEvent {
  path: string;
  files_done: number;
  files_total: number;
  entities: number;
  triples: number;
}

export interface ReindexStatsEvent {
  files_done: number;
  files_total: number;
  files_indexed: number;
  files_skipped: number;
  entities: number;
  triples: number;
  entities_added: number;
  triples_added: number;
  elapsed_s: number;
}

export type ReindexEvent =
  | { type: "status"; message: string }
  | { type: "file" } & ReindexFileEvent
  | { type: "error"; path?: string; detail: string }
  | { type: "stats" } & ReindexStatsEvent
  | { type: "done" };

export async function graphragReindex(
  onEvent: (e: ReindexEvent) => void,
  signal?: AbortSignal,
  full = false,
): Promise<void> {
  const url = `${BASE}/graphrag/reindex${full ? "?full=1" : ""}`;
  const res = await fetch(url, {
    method: "POST",
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch { /* ignore */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";

    for (const frame of frames) {
      if (!frame.trim()) continue;
      let eventName = "message";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
      }
      if (!dataLine) continue;
      try {
        const parsed = JSON.parse(dataLine) as Record<string, unknown>;
        if (eventName === "status") {
          onEvent({ type: "status", message: parsed.message as string });
        } else if (eventName === "file") {
          onEvent({ type: "file", ...(parsed as Omit<ReindexFileEvent, "type">) } as ReindexEvent);
        } else if (eventName === "error") {
          onEvent({ type: "error", path: parsed.path as string | undefined, detail: parsed.detail as string });
        } else if (eventName === "stats") {
          onEvent({ type: "stats", ...(parsed as Omit<ReindexStatsEvent, "type">) } as ReindexEvent);
        } else if (eventName === "done") {
          onEvent({ type: "done" });
        }
      } catch { /* malformed frame — skip */ }
    }
  }
}

export async function getInsights(days = 30, model?: string): Promise<InsightsReport> {
  const q = new URLSearchParams({ days: String(days) });
  if (model) q.set("model", model);
  const res = await fetch(`${BASE}/insights?${q.toString()}`);
  if (!res.ok) throw new Error(`Insights error: ${res.status}`);
  return res.json();
}

// ── HITL (human-in-the-loop) ──────────────────────────────────────────────

export interface UserRequestPayload {
  request_id: string;
  prompt: string;
  kind: "confirm" | "choice" | "text";
  choices: string[] | null;
  default: string | null;
  timeout_seconds: number;
}

/**
 * One event from the session-scoped SSE channel. Separate from the
 * existing `/chat/stream` events, which are per-turn content deltas.
 * Opened once per session on mount; carries every trace + HITL event
 * until the EventSource is closed.
 */
export type SessionEvent =
  | { kind: "iter"; data: { n: number } }
  | { kind: "tool_call"; data: { name: string; args: unknown } }
  | { kind: "tool_result"; data: { name: string; preview: string } }
  | { kind: "reply"; data: { text: string } }
  | { kind: "user_request"; data: UserRequestPayload }
  | { kind: "user_request_auto"; data: { prompt: string; answer: string; reason: string } }
  | { kind: "user_request_cancelled"; data: { request_id: string; reason: string } };

/**
 * Open an EventSource on the session's event stream.
 *
 * Typed callback fires for every known event kind; unknown kinds are
 * silently dropped so a server adding new events doesn't break old
 * UIs. Returns the underlying EventSource so the caller can close it
 * on unmount — closing is the *only* cleanup required.
 */
export function subscribeSessionEvents(
  session_id: string,
  onEvent: (event: SessionEvent) => void,
): EventSource {
  const url = `${BASE}/chat/${encodeURIComponent(session_id)}/events`;
  const es = new EventSource(url);

  const kinds: SessionEvent["kind"][] = [
    "iter",
    "tool_call",
    "tool_result",
    "reply",
    "user_request",
    "user_request_auto",
    "user_request_cancelled",
  ];
  for (const kind of kinds) {
    es.addEventListener(kind, (evt) => {
      try {
        const data = JSON.parse((evt as MessageEvent).data);
        onEvent({ kind, data } as SessionEvent);
      } catch {
        // Malformed server event — skip rather than crashing the UI.
      }
    });
  }

  return es;
}

export async function fetchPendingRequest(
  session_id: string,
): Promise<UserRequestPayload | null> {
  const res = await fetch(
    `${BASE}/chat/${encodeURIComponent(session_id)}/pending`,
  );
  if (!res.ok) return null;
  const body = (await res.json()) as { pending: UserRequestPayload | null };
  return body.pending ?? null;
}

export async function respondToUserRequest(
  session_id: string,
  request_id: string,
  answer: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/chat/${encodeURIComponent(session_id)}/respond`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id, answer }),
    },
  );
  if (!res.ok && res.status !== 404) {
    // 404 is expected on a stale request (timed out, session reset) —
    // the UI treats it as a no-op and the dialog is already closed.
    throw new Error(`Respond error: ${res.status}`);
  }
}

// ── HITL settings (YOLO mode) ─────────────────────────────────────────────

export interface HitlSettings {
  yolo_mode: boolean;
}

export async function getHitlSettings(): Promise<HitlSettings> {
  const res = await fetch(`${BASE}/settings`);
  if (!res.ok) throw new Error(`Settings error: ${res.status}`);
  return res.json();
}

export async function setHitlSettings(
  patch: Partial<HitlSettings>,
): Promise<HitlSettings> {
  const res = await fetch(`${BASE}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Settings patch error: ${res.status}`);
  return res.json();
}
