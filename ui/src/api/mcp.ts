/** MCP server management API. */

import { BASE } from "./base";

export interface McpServerStatus {
  name: string;
  connected: boolean;
  tool_count?: number;
  tools?: string[];
}

export interface McpTestResult {
  ok: boolean;
  tool_count: number;
  tools: string[];
  error: string | null;
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

export async function reloadMcpServers(): Promise<{
  ok: boolean;
  tool_count: number;
  servers: string[];
}> {
  const res = await fetch(`${BASE}/mcp/reload`, { method: "POST" });
  if (!res.ok) throw new Error(`reloadMcpServers: ${res.status}`);
  return res.json();
}

export async function testMcpServer(
  config: Record<string, unknown>,
): Promise<McpTestResult> {
  const res = await fetch(`${BASE}/mcp/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error(`testMcpServer: ${res.status}`);
  return res.json();
}

export async function fetchMcpAppResource(
  serverName: string,
  uri: string,
): Promise<{ ok: boolean; html: string; server: string; uri: string }> {
  const res = await fetch(
    `${BASE}/mcp/app/${encodeURIComponent(serverName)}?uri=${encodeURIComponent(uri)}`,
  );
  if (!res.ok) throw new Error(`fetchMcpAppResource: ${res.status}`);
  return res.json();
}

export interface McpToolInfo {
  name: string;
  description: string;
  meta?: { ui?: { resourceUri?: string } };
}

export async function fetchMcpTools(): Promise<McpToolInfo[]> {
  const res = await fetch(`${BASE}/mcp/tools`);
  if (!res.ok) return [];
  return res.json();
}

export async function callMcpTool(
  serverName: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<{ ok: boolean; text: string; is_error: boolean }> {
  const res = await fetch(`${BASE}/mcp/call-tool`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server_name: serverName, tool_name: toolName, arguments: args }),
  });
  if (!res.ok) throw new Error(`callMcpTool: ${res.status}`);
  return res.json();
}

export async function fetchInternalResource(
  uri: string,
): Promise<{ ok: boolean; html: string; uri: string }> {
  const res = await fetch(
    `${BASE}/mcp/internal-resource?uri=${encodeURIComponent(uri)}`,
  );
  if (!res.ok) throw new Error(`fetchInternalResource: ${res.status}`);
  return res.json();
}
