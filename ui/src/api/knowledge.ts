// API client for knowledge graph (GraphRAG) endpoints.
import { BASE } from "./base";

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

export interface KnowledgeEntity {
  id: number;
  name: string;
  type: string;
  degree: number;
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

export interface SubgraphData {
  enabled: boolean;
  nodes: { id: number; name: string; type: string; degree: number }[];
  edges: { source: number; target: number; relation: string; strength: number }[];
}

export interface GraphragIndexFileResult {
  queued?: boolean;
  enabled?: boolean;
  reason?: string;
  path?: string;
}

export interface GraphragIndexStatus {
  status: "unknown" | "indexing" | "done" | "error" | "cancelled";
  node_count?: number;
  edge_count?: number;
  detail?: string;
  total_chunks?: number;
  processed_chunks?: number;
  nodes?: { id: number; name: string; type: string; degree?: number }[];
  edges?: { source: number; target: number; relation: string; strength: number }[];
}

export interface KnowledgeStats {
  enabled: boolean;
  entities: number;
  triples: number;
  types: Record<string, number>;
  components: { id: number; size: number; entities: number[] }[];
  component_count: number;
}

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

export async function getKnowledgeGraph(): Promise<KnowledgeGraphData> {
  const res = await fetch(`${BASE}/graph/knowledge`);
  if (!res.ok) throw new Error(`Knowledge graph error: ${res.status}`);
  return res.json() as Promise<KnowledgeGraphData>;
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

export async function getKnowledgeEntity(id: number): Promise<EntityDetail> {
  const res = await fetch(`${BASE}/graph/knowledge/entity/${id}`);
  if (!res.ok) throw new Error(`Entity detail error: ${res.status}`);
  return res.json() as Promise<EntityDetail>;
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

export async function graphragIndexFile(path: string): Promise<GraphragIndexFileResult> {
  const res = await fetch(`${BASE}/graph/knowledge/index-file`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`Index file error: ${res.status}`);
  return res.json();
}

export async function getGraphragIndexStatus(path: string): Promise<GraphragIndexStatus> {
  const res = await fetch(`${BASE}/graph/knowledge/index-file-status?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Index status error: ${res.status}`);
  return res.json();
}

export async function cancelGraphragIndexFile(path: string): Promise<{ cancelled: boolean }> {
  const res = await fetch(`${BASE}/graph/knowledge/index-file-cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`Cancel error: ${res.status}`);
  return res.json();
}

export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  const res = await fetch(`${BASE}/graph/knowledge/stats`);
  if (!res.ok) throw new Error(`Knowledge stats error: ${res.status}`);
  return res.json() as Promise<KnowledgeStats>;
}

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
