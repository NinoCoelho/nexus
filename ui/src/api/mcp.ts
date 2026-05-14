/** MCP server management API. */

import { BASE } from "./base";

export interface McpServerStatus {
  name: string;
  connected: boolean;
  tool_count?: number;
  tools?: string[];
}

export async function listMcpServers(): Promise<McpServerStatus[]> {
  const res = await fetch(`${BASE}/mcp/servers`);
  if (!res.ok) throw new Error(`listMcpServers: ${res.status}`);
  return res.json();
}

export async function reconnectMcpServer(
  serverName: string,
): Promise<{ ok: boolean; server: string; connected: boolean }> {
  const res = await fetch(`${BASE}/mcp/servers/${encodeURIComponent(serverName)}/reconnect`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`reconnectMcpServer: ${res.status}`);
  return res.json();
}

export async function refreshMcpTools(): Promise<{
  ok: boolean;
  tool_count: number;
  servers: string[];
}> {
  const res = await fetch(`${BASE}/mcp/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(`refreshMcpTools: ${res.status}`);
  return res.json();
}
