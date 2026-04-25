// API client for usage insights and agent graph.
import { BASE } from "./base";

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

export async function getInsights(days = 30, model?: string): Promise<InsightsReport> {
  const q = new URLSearchParams({ days: String(days) });
  if (model) q.set("model", model);
  const res = await fetch(`${BASE}/insights?${q.toString()}`);
  if (!res.ok) throw new Error(`Insights error: ${res.status}`);
  return res.json();
}

export async function getAgentGraph(): Promise<AgentGraphData> {
  const res = await fetch(`${BASE}/graph`);
  if (!res.ok) throw new Error(`Agent graph error: ${res.status}`);
  return res.json() as Promise<AgentGraphData>;
}
