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

export async function getSessions(limit = 50): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE}/sessions?limit=${limit}`);
  if (!res.ok) throw new Error(`Sessions error: ${res.status}`);
  return res.json();
}

export async function getSession(id: string): Promise<SessionDetail> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Session error: ${res.status}`);
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

// ── Vault ─────────────────────────────────────────────────────────────────────

export interface VaultNode {
  path: string;
  type: "file" | "dir";
  size?: number;
  mtime?: string;
}

export interface VaultFile {
  path: string;
  content: string;
  frontmatter?: Record<string, unknown>;
  body?: string;
}

export async function getVaultTree(): Promise<VaultNode[]> {
  const res = await fetch(`${BASE}/vault/tree`);
  if (!res.ok) throw new Error(`Vault tree error: ${res.status}`);
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

export async function deleteVaultFile(path: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/file?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
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

export interface GraphNode { path: string; size: number; folder: string }
export interface GraphEdge { from: string; to: string }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[]; orphans: string[] }

export async function getVaultGraph(): Promise<GraphData> {
  const res = await fetch(`${BASE}/vault/graph`);
  if (!res.ok) throw new Error(`Vault graph error: ${res.status}`);
  return res.json() as Promise<GraphData>;
}

// ── Kanban ────────────────────────────────────────────────────────────────────

export interface KanbanCard {
  id: string;
  title: string;
  column: string;
  notes?: string;
  tags?: string[];
  created_at: string;
  updated_at: string;
}

export interface KanbanBoard {
  columns: string[];
  cards: KanbanCard[];
}

export interface Board {
  name: string;
  card_count: number;
}

export async function getKanbanBoards(): Promise<Board[]> {
  const res = await fetch(`${BASE}/kanban/boards`);
  if (!res.ok) throw new Error(`Kanban boards error: ${res.status}`);
  return res.json();
}

export async function postKanbanBoard(name: string): Promise<Board> {
  const res = await fetch(`${BASE}/kanban/boards`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Kanban board create error: ${res.status}`);
  return res.json();
}

export async function deleteKanbanBoard(name: string): Promise<void> {
  const res = await fetch(`${BASE}/kanban/boards/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Kanban board delete error: ${res.status}`);
  }
}

export async function getKanban(board = "default"): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/kanban?board=${encodeURIComponent(board)}`);
  if (!res.ok) throw new Error(`Kanban error: ${res.status}`);
  return res.json();
}

export async function postKanbanCard(
  card: { title: string; column: string; notes?: string; tags?: string[] },
  board = "default",
): Promise<KanbanCard> {
  const res = await fetch(`${BASE}/kanban/cards?board=${encodeURIComponent(board)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(card),
  });
  if (!res.ok) throw new Error(`Kanban card create error: ${res.status}`);
  return res.json();
}

export async function patchKanbanCard(
  id: string,
  patch: { title?: string; notes?: string; tags?: string[]; column?: string },
  board = "default",
): Promise<KanbanCard> {
  const res = await fetch(`${BASE}/kanban/cards/${encodeURIComponent(id)}?board=${encodeURIComponent(board)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Kanban card patch error: ${res.status}`);
  return res.json();
}

export async function deleteKanbanCard(id: string, board = "default"): Promise<void> {
  const res = await fetch(`${BASE}/kanban/cards/${encodeURIComponent(id)}?board=${encodeURIComponent(board)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban card delete error: ${res.status}`);
}

export async function postKanbanColumn(name: string, board = "default"): Promise<void> {
  const res = await fetch(`${BASE}/kanban/columns?board=${encodeURIComponent(board)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Kanban column create error: ${res.status}`);
}

export async function deleteKanbanColumn(name: string, board = "default"): Promise<void> {
  const res = await fetch(`${BASE}/kanban/columns/${encodeURIComponent(name)}?board=${encodeURIComponent(board)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban column delete error: ${res.status}`);
}

export interface TraceEvent {
  iter: number;
  tool?: string;
  args?: unknown;
  result?: unknown;
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
  | { type: "done"; session_id: string; reply: string; trace: TraceEvent[]; skills_touched: string[] }
  | { type: "error"; detail: string };

export async function chatStream(
  message: string,
  session_id: string | undefined,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id }),
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
          onEvent({
            type: "done",
            session_id: parsed.session_id as string,
            reply: parsed.reply as string,
            trace: (parsed.trace ?? []) as TraceEvent[],
            skills_touched: (parsed.skills_touched ?? []) as string[],
          });
        } else if (eventName === "error") {
          onEvent({ type: "error", detail: parsed.detail as string });
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

export async function getHealth(): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

// ── Settings types ────────────────────────────────────────────────────────────

export interface ModelStrengths {
  speed: number;
  cost: number;
  reasoning: number;
  coding: number;
}

export interface Model {
  id: string;
  provider: string;
  model_name: string;
  tags: string[];
  strengths: ModelStrengths;
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

export interface Config {
  agent: AgentConfig;
  providers: Record<string, { base_url?: string; key_env?: string; has_key: boolean }>;
  models: Model[];
}

export interface RoutingConfig {
  mode: "fixed" | "auto";
  default_model: string;
  available_models: string[];
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

export async function getRouting(): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`);
  if (!res.ok) throw new Error(`Routing error: ${res.status}`);
  return res.json();
}

export async function putRouting(patch: { mode?: "fixed" | "auto"; default_model?: string }): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Routing update error: ${res.status}`);
  return res.json();
}
