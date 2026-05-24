import { BASE } from "./base";

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
