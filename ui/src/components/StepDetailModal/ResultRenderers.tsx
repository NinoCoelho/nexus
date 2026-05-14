// StepDetailModal — terminal, HTTP, KV-table, and FormattedResult dispatching renderers.

import { useCallback, useEffect, useState } from "react";
import type { TraceEvent } from "../../api";
import { callMcpTool, fetchInternalResource, fetchMcpAppResource, fetchMcpTools, type McpToolInfo } from "../../api/mcp";
import McpAppSandbox from "../McpAppSandbox";
import MarkdownView from "../MarkdownView";
import {
  tryParseJson, isTerminalResult, isHttpResult, isMarkdownLike,
  type TerminalResult, type HttpResult, type VaultEntry, type SearchMatch,
} from "./types";
import {
  VaultReadResult, VaultListResult, VaultSearchResult,
  VaultTagsResult, VaultBacklinksResult,
} from "./VaultResultRenderers";

// ── Tool meta cache (fetched once, shared across all FormattedResult instances) ──

let toolMetaCache: Record<string, McpToolInfo> | null = null;
let toolMetaPromise: Promise<Record<string, McpToolInfo>> | null = null;

async function getToolMeta(): Promise<Record<string, McpToolInfo>> {
  if (toolMetaCache) return toolMetaCache;
  if (toolMetaPromise) return toolMetaPromise;
  toolMetaPromise = fetchMcpTools()
    .then((tools) => {
      const map: Record<string, McpToolInfo> = {};
      for (const t of tools) map[t.name] = t;
      toolMetaCache = map;
      return map;
    })
    .catch(() => {
      toolMetaCache = {};
      return toolMetaCache;
    });
  return toolMetaPromise;
}

export function invalidateToolMetaCache(): void {
  toolMetaCache = null;
  toolMetaPromise = null;
}

function McpAppRenderer({ serverName, resourceUri, toolResult }: { serverName: string; resourceUri: string; toolResult: unknown }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMcpAppResource(serverName, resourceUri)
      .then((res) => {
        if (!cancelled) setHtml(res.html);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load MCP App");
      });
    return () => { cancelled = true; };
  }, [serverName, resourceUri]);

  const handleToolCall = useCallback(
    async (name: string, args: Record<string, unknown>) => {
      return callMcpTool(serverName, name, args);
    },
    [serverName],
  );

  if (error) return <div className="sdm-log-result" style={{ color: "var(--color-error, #c53030)" }}>MCP App: {error}</div>;
  if (!html) return <div className="sdm-log-result">Loading MCP App…</div>;
  return <McpAppSandbox html={html} toolResult={toolResult} onToolCall={handleToolCall} />;
}

// ── MCP App wrapper that resolves resourceUri from tool definition meta ──

function McpAppFromToolMeta({ tool, toolResult }: { tool: string; toolResult: unknown }) {
  const [resourceUri, setResourceUri] = useState<string | null>(null);
  const serverName = tool.split("__")[0];

  useEffect(() => {
    let cancelled = false;
    getToolMeta().then((meta) => {
      if (cancelled) return;
      const info = meta[tool];
      setResourceUri(info?.meta?.ui?.resourceUri ?? null);
    });
    return () => { cancelled = true; };
  }, [tool]);

  if (!resourceUri) return null;
  return <McpAppRenderer serverName={serverName} resourceUri={resourceUri} toolResult={toolResult} />;
}

// ── Internal nexus resource renderer (ui://nexus/*) ──

export function InternalResourceRenderer({ resourceUri, toolResult }: { resourceUri: string; toolResult: unknown }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchInternalResource(resourceUri)
      .then((res) => {
        if (!cancelled) setHtml(res.html);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load resource");
      });
    return () => { cancelled = true; };
  }, [resourceUri]);

  const handleToolCall = useCallback(
    async (name: string, args: Record<string, unknown>) => {
      return callMcpTool("nexus", name, args);
    },
    [],
  );

  if (error) return <div className="sdm-log-result" style={{ color: "var(--color-error, #c53030)" }}>{error}</div>;
  if (!html) return <div className="sdm-log-result">Loading…</div>;
  return <McpAppSandbox html={html} toolResult={toolResult} onToolCall={handleToolCall} />;
}

// ── Resolve resourceUri from tool meta for internal nexus tools ──

function NexusToolApp({ tool, toolResult }: { tool: string; toolResult: unknown }) {
  const [resourceUri, setResourceUri] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getToolMeta().then((meta) => {
      if (cancelled) return;
      const info = meta[tool];
      const uri = info?.meta?.ui?.resourceUri;
      if (uri && uri.startsWith("ui://nexus/")) {
        setResourceUri(uri);
      }
    });
    return () => { cancelled = true; };
  }, [tool]);

  if (!resourceUri) return null;

  // If the URI has template params like {path}, try to substitute from toolResult
  let resolvedUri = resourceUri;
  if (toolResult && typeof toolResult === "object") {
    const parsed = tryParseJson(toolResult);
    if (parsed && typeof parsed === "object") {
      const data = parsed as Record<string, unknown>;
      resolvedUri = resourceUri.replace(/\{(\w+)\}/g, (_, key) =>
        typeof data[key] === "string" ? data[key] : String(data[key] ?? ""),
      );
    }
  }

  return <InternalResourceRenderer resourceUri={resolvedUri} toolResult={toolResult} />;
}

export function TerminalOutput({ data }: { data: TerminalResult }) {
  return (
    <div className="sdm-result-terminal">
      <div className="sdm-term-meta">
        {data.exit_code != null && (
          <span className={`sdm-term-exit${data.ok ? " sdm-term-exit--ok" : " sdm-term-exit--fail"}`}>
            exit {data.exit_code}
          </span>
        )}
        {data.duration_ms != null && data.duration_ms > 0 && (
          <span className="sdm-term-duration">{data.duration_ms >= 1000 ? `${(data.duration_ms / 1000).toFixed(1)}s` : `${data.duration_ms}ms`}</span>
        )}
        {data.timed_out && <span className="sdm-term-badge sdm-term-badge--warn">timed out</span>}
        {data.denied && <span className="sdm-term-badge sdm-term-badge--warn">denied</span>}
      </div>
      {data.error && !data.denied && (
        <div className="sdm-term-error">{data.error}</div>
      )}
      {data.stdout && (
        <div className="sdm-term-section">
          <span className="sdm-term-label">stdout</span>
          <pre className="sdm-term-output">{data.stdout}</pre>
          {data.stdout_truncated && <span className="sdm-term-truncated">output truncated</span>}
        </div>
      )}
      {data.stderr && (
        <div className="sdm-term-section">
          <span className="sdm-term-label sdm-term-label--err">stderr</span>
          <pre className="sdm-term-output sdm-term-output--err">{data.stderr}</pre>
          {data.stderr_truncated && <span className="sdm-term-truncated">output truncated</span>}
        </div>
      )}
      {!data.stdout && !data.stderr && !data.error && (
        <div className="sdm-term-empty">(no output)</div>
      )}
    </div>
  );
}

export function HttpOutput({ data }: { data: HttpResult }) {
  const looksLikeMarkdown = (s: string) =>
    /^#{1,6} |\*\*|^- |^> |```/.test(s);

  return (
    <div className="sdm-result-http">
      <div className="sdm-http-meta">
        {data.status != null && (
          <span className={`sdm-http-status${data.ok ? " sdm-http-status--ok" : " sdm-http-status--fail"}`}>
            {data.status}
          </span>
        )}
      </div>
      {data.error && <div className="sdm-term-error">{data.error}</div>}
      {data.body && (() => {
        const body = data.body.length > 4000 ? data.body.slice(0, 4000) + "\n…" : data.body;
        const jsonParsed = tryParseJson(body);
        if (jsonParsed && typeof jsonParsed === "object") {
          return (
            <div className="sdm-result-markdown">
              <MarkdownView>{"```json\n" + JSON.stringify(jsonParsed, null, 2) + "\n```"}</MarkdownView>
            </div>
          );
        }
        if (looksLikeMarkdown(body)) {
          return <div className="sdm-result-markdown"><MarkdownView>{body}</MarkdownView></div>;
        }
        return <pre className="sdm-term-output">{body}</pre>;
      })()}
    </div>
  );
}

export function KvTable({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([k]) => k !== "ok");
  if (!entries.length) return null;
  return (
    <table className="sdm-kv-table">
      <tbody>
        {entries.map(([k, v]) => {
          const display = typeof v === "string"
            ? v
            : Array.isArray(v)
            ? `[${(v as unknown[]).length} items]`
            : typeof v === "object" && v !== null
            ? JSON.stringify(v)
            : String(v);
          return (
            <tr key={k}>
              <td className="sdm-kv-key">{k}</td>
              <td className="sdm-kv-val">{display.length > 200 ? display.slice(0, 200) + "…" : display}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function resolveResult(stepResult: unknown, tool: string, trace?: TraceEvent[], stepIdx?: number): unknown {
  if (trace && stepIdx !== undefined) {
    const toolTraces = trace.filter((t) => t.tool === tool);
    const match = toolTraces[stepIdx];
    if (match?.result != null) return match.result;
  }
  return stepResult;
}

export function FormattedResult({ tool, result, trace, stepIdx }: { tool?: string; result: unknown; trace?: TraceEvent[]; stepIdx?: number }) {
  const resolved = resolveResult(result, tool ?? "", trace, stepIdx);
  const parsed = tryParseJson(resolved);

  if (parsed && isTerminalResult(parsed)) return <TerminalOutput data={parsed} />;
  if (parsed && isHttpResult(parsed)) return <HttpOutput data={parsed} />;

  // MCP App — namespaced tool (server__tool). Check tool definition meta
  // for resourceUri, with fallback to _meta in the result JSON.
  if (tool && tool.includes("__")) {
    if (parsed && typeof parsed === "object") {
      const meta = (parsed as Record<string, unknown>)._meta as Record<string, unknown> | undefined;
      const ui = meta?.ui as Record<string, unknown> | undefined;
      const resourceUri = ui?.resourceUri as string | undefined;
      if (resourceUri) {
        const serverName = tool.split("__")[0];
        return <McpAppRenderer serverName={serverName} resourceUri={resourceUri} toolResult={parsed} />;
      }
    }
    return <McpAppFromToolMeta tool={tool} toolResult={resolved} />;
  }

  // Internal nexus tools with ui://nexus/* resourceUri (show_kanban, show_dashboard_widget, show_data_table)
  if (tool && (tool === "show_kanban" || tool === "show_dashboard_widget" || tool === "show_data_table")) {
    // First try resourceUri from tool result
    if (parsed && typeof parsed === "object") {
      const resultUri = (parsed as Record<string, unknown>).resourceUri as string | undefined;
      if (resultUri && resultUri.startsWith("ui://nexus/")) {
        return <InternalResourceRenderer resourceUri={resultUri} toolResult={parsed} />;
      }
    }
    // Fall back to tool meta cache
    return <NexusToolApp tool={tool} toolResult={resolved} />;
  }

  // Vault read — render content as markdown
  if (tool === "vault_read" && parsed && typeof parsed === "object") {
    const p = parsed as Record<string, unknown>;
    if (typeof p.content === "string") return <VaultReadResult content={p.content} />;
  }

  // Vault list — render file tree
  if (tool === "vault_list" && parsed && typeof parsed === "object") {
    const p = parsed as Record<string, unknown>;
    if (Array.isArray(p.entries)) return <VaultListResult entries={p.entries as VaultEntry[]} />;
  }

  // Vault search — render match cards
  if ((tool === "vault_search" || tool === "vault_semantic_search") && parsed && typeof parsed === "object") {
    const p = parsed as Record<string, unknown>;
    if (Array.isArray(p.results)) return <VaultSearchResult results={p.results as SearchMatch[]} />;
  }

  // Vault tags — render tag list or file list
  if (tool === "vault_tags" && parsed && typeof parsed === "object") {
    const p = parsed as Record<string, unknown>;
    const items = Array.isArray(p.tags) ? p.tags as string[] : Array.isArray(p.files) ? p.files as string[] : null;
    if (items) return <VaultTagsResult items={items} />;
  }

  // Vault backlinks — render as link list
  if (tool === "vault_backlinks" && parsed && typeof parsed === "object") {
    const p = parsed as Record<string, unknown>;
    const links = Array.isArray(p.backlinks) ? p.backlinks as string[] : null;
    if (links) return <VaultBacklinksResult links={links} />;
  }

  const asStr = typeof resolved === "string" ? resolved : resolved != null ? JSON.stringify(resolved) : null;
  if (!asStr) return null;

  // String that looks like markdown — render it
  if (typeof resolved === "string" && isMarkdownLike(resolved)) {
    return <div className="sdm-result-markdown"><MarkdownView>{resolved}</MarkdownView></div>;
  }

  // Generic JSON object — KV table
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    return <KvTable data={parsed as Record<string, unknown>} />;
  }

  // Plain text fallback
  return (
    <div className="sdm-log-result">
      <span className="sdm-log-arrow">→</span>
      {asStr.length > 600 ? asStr.slice(0, 600) + "…" : asStr}
    </div>
  );
}
