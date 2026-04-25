// API client for vault graph and entity sources.
import { BASE } from "./base";

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

export async function getVaultEntitySources(path: string): Promise<{ path: string; entities: { id: number; name: string; type: string }[] }> {
  const res = await fetch(`${BASE}/vault/graph/entity-sources?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Vault entity sources error: ${res.status}`);
  return res.json();
}
