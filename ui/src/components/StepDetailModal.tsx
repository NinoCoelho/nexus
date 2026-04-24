import React, { useEffect, useRef } from "react";
import type { TraceEvent } from "../api";
import type { CoalescedStep } from "./ActivityTimeline";
import MarkdownView from "./MarkdownView";
import "./StepDetailModal.css";

interface TerminalResult {
  ok: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  stdout_truncated?: boolean;
  stderr_truncated?: boolean;
  duration_ms?: number;
  timed_out?: boolean;
  denied?: boolean;
  error?: string | null;
}

interface HttpResult {
  status: number | null;
  ok: boolean;
  body: string;
  error?: string | null;
}

function metaLabel(tool: string): string {
  const map: Record<string, string> = {
    vault_list: "Listing vault",
    vault_read: "Reading",
    vault_write: "Writing",
    vault_search: "Searching vault",
    vault_tags: "Tags",
    vault_backlinks: "Backlinks",
    kanban_manage: "Kanban",
    http_call: "HTTP Request",
    terminal: "Terminal",
    skill_manage: "Authoring skill",
    skill_view: "Reading skill",
    skills_list: "Listing skills",
  };
  return map[tool] ?? tool.replace(/_/g, " ");
}

function ToolIcon({ tool }: { tool: string }) {
  switch (tool) {
    case "vault_list":
    case "vault_read":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
          <polyline points="9 1.5 9 5 12 5" />
        </svg>
      );
    case "vault_write":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2z" />
        </svg>
      );
    case "vault_search":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="6.5" cy="6.5" r="4" />
          <line x1="9.5" y1="9.5" x2="13" y2="13" />
        </svg>
      );
    case "http_call":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="8" cy="8" r="6" />
          <path d="M2 8h12M8 2a9 9 0 0 1 0 12M8 2a9 9 0 0 0 0 12" />
        </svg>
      );
    case "terminal":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="2 3.5 6 7.5 2 11.5" />
          <line x1="8" y1="11.5" x2="14" y2="11.5" />
        </svg>
      );
    case "kanban_manage":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="3" width="3" height="8" rx="0.5" />
          <rect x="6.5" y="3" width="3" height="5" rx="0.5" />
          <rect x="11" y="3" width="3" height="10" rx="0.5" />
        </svg>
      );
    case "skill_manage":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2z" />
        </svg>
      );
    case "skill_view":
    case "skills_list":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 2.5A1.5 1.5 0 0 1 4.5 1H12v13H4.5A1.5 1.5 0 0 1 3 12.5z" />
          <line x1="3" y1="12.5" x2="12" y2="12.5" />
        </svg>
      );
    case "vault_tags":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <line x1="5" y1="3" x2="5" y2="13" />
          <line x1="11" y1="3" x2="11" y2="13" />
          <line x1="2.5" y1="6" x2="13.5" y2="6" />
          <line x1="2.5" y1="10" x2="13.5" y2="10" />
        </svg>
      );
    case "vault_backlinks":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6.5 9.5a3.536 3.536 0 0 0 5 0l2-2a3.536 3.536 0 0 0-5-5L7 4" />
          <path d="M9.5 6.5a3.536 3.536 0 0 0-5 0l-2 2a3.536 3.536 0 0 0 5 5L9 12" />
        </svg>
      );
    default:
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="8" cy="8" r="2.5" fill="currentColor" stroke="none" />
        </svg>
      );
  }
}

function tryParseJson(val: unknown): unknown {
  if (val == null) return null;
  if (typeof val === "object") return val;
  if (typeof val !== "string") return null;
  try {
    return JSON.parse(val);
  } catch {
    return null;
  }
}

function isTerminalResult(obj: unknown): obj is TerminalResult {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return typeof o.ok === "boolean" && "exit_code" in o && "stdout" in o && "stderr" in o;
}

function isHttpResult(obj: unknown): obj is HttpResult {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return typeof o.ok === "boolean" && "status" in o && "body" in o;
}

function resolveResult(stepResult: unknown, tool: string, trace?: TraceEvent[], stepIdx?: number): unknown {
  if (trace && stepIdx !== undefined) {
    const toolTraces = trace.filter((t) => t.tool === tool);
    const match = toolTraces[stepIdx];
    if (match?.result != null) return match.result;
  }
  return stepResult;
}

function statusDot(status?: string) {
  if (status === "pending") return <span className="sdm-log-dot sdm-log-dot--pending" />;
  if (status === "error") return <span className="sdm-log-dot sdm-log-dot--error" />;
  return <span className="sdm-log-dot sdm-log-dot--done" />;
}

function statusText(status?: string): string {
  if (status === "pending") return "Running";
  if (status === "error") return "Error";
  return "Done";
}

// ── Humanized args ─────────────────────────────────────────────────────────

function ToolArgsSummary({ tool, args }: { tool?: string; args: unknown }) {
  if (!tool || !args || typeof args !== "object") return null;
  const a = args as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" ? v : "");

  let content: React.ReactNode = null;

  switch (tool) {
    case "vault_read":
      content = <>Reading <code className="sdm-arg-code">{str(a.path)}</code></>;
      break;
    case "vault_write": {
      const chars = typeof a.content === "string" ? a.content.length : null;
      content = (
        <>
          Writing to <code className="sdm-arg-code">{str(a.path)}</code>
          {chars != null && <span className="sdm-arg-dim"> · {chars.toLocaleString()} chars</span>}
        </>
      );
      break;
    }
    case "vault_list":
      content = <>Listing <code className="sdm-arg-code">{str(a.path) || "/"}</code></>;
      break;
    case "vault_search":
    case "vault_semantic_search":
      content = <>Searching for <span className="sdm-arg-query">"{str(a.query)}"</span></>;
      break;
    case "vault_tags":
      content = a.path
        ? <>Tags on <code className="sdm-arg-code">{str(a.path)}</code></>
        : <>Listing all tags</>;
      break;
    case "vault_backlinks":
      content = <>Backlinks for <code className="sdm-arg-code">{str(a.path)}</code></>;
      break;
    case "http_call": {
      const method = str(a.method) || "GET";
      const url = str(a.url);
      content = (
        <>
          <span className="sdm-arg-method">{method}</span>{" "}
          <code className="sdm-arg-code sdm-arg-url">{url.length > 80 ? url.slice(0, 80) + "…" : url}</code>
        </>
      );
      break;
    }
    case "terminal":
      content = <code className="sdm-arg-code sdm-arg-cmd">{str(a.command)}</code>;
      break;
    case "skill_manage": {
      const action = str(a.action);
      const name = str(a.name);
      content = (
        <>
          {action || "manage"} skill{name ? <> <strong>{name}</strong></> : null}
        </>
      );
      break;
    }
    case "skill_view":
      content = <>Reading skill <strong>{str(a.name)}</strong></>;
      break;
    case "skills_list":
      content = <>Listing skills</>;
      break;
    case "kanban_manage": {
      const action = str(a.action);
      const board = str(a.board ?? a.path ?? "");
      content = (
        <>
          {action || "manage"}{board ? <> on <code className="sdm-arg-code">{board}</code></> : null}
        </>
      );
      break;
    }
    default: {
      const entries = Object.entries(a).filter(([, v]) => v != null);
      if (entries.length === 0) return null;
      content = (
        <>
          {entries.map(([k, v], i) => (
            <span key={k}>
              {i > 0 && <span className="sdm-arg-dim"> · </span>}
              <span className="sdm-arg-dim">{k}:</span>{" "}
              <code className="sdm-arg-code">
                {typeof v === "string"
                  ? v.length > 80 ? v.slice(0, 80) + "…" : v
                  : JSON.stringify(v)}
              </code>
            </span>
          ))}
        </>
      );
    }
  }

  if (!content) return null;
  return <p className="sdm-args-prose">{content}</p>;
}

// ── Result renderers ───────────────────────────────────────────────────────

function TerminalOutput({ data }: { data: TerminalResult }) {
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

function HttpOutput({ data }: { data: HttpResult }) {
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

function VaultReadResult({ content }: { content: string }) {
  return (
    <div className="sdm-result-markdown">
      <MarkdownView>{content}</MarkdownView>
    </div>
  );
}

interface VaultEntry { path: string; type: string; size: number; }
function VaultListResult({ entries }: { entries: VaultEntry[] }) {
  if (!entries.length) return <div className="sdm-empty">No files found</div>;
  return (
    <ul className="sdm-file-list">
      {entries.map((e) => {
        const parts = e.path.split("/");
        const name = parts.pop() ?? e.path;
        const dir = parts.join("/");
        const isDir = e.type === "dir";
        return (
          <li key={e.path} className="sdm-file-item">
            <span className="sdm-file-icon">
              {isDir ? (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5H12A1.5 1.5 0 0 1 13.5 6v5A1.5 1.5 0 0 1 12 12.5H4A1.5 1.5 0 0 1 2.5 11z" />
                </svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
                  <polyline points="9 1.5 9 5 12 5" />
                </svg>
              )}
            </span>
            <span className="sdm-file-name">{name}</span>
            {dir && <span className="sdm-file-dir">{dir}/</span>}
          </li>
        );
      })}
    </ul>
  );
}

interface SearchMatch { path: string; snippet: string; score: number; }
function VaultSearchResult({ results }: { results: SearchMatch[] }) {
  if (!results.length) return <div className="sdm-empty">No matches found</div>;
  return (
    <div className="sdm-search-results">
      {results.map((r, i) => {
        const parts = r.path.split("/");
        const name = parts.pop() ?? r.path;
        const dir = parts.join("/");
        // Convert <mark>…</mark> to bold markdown
        const snippet = r.snippet.replace(/<mark>/g, "**").replace(/<\/mark>/g, "**");
        return (
          <div key={i} className="sdm-search-match">
            <div className="sdm-match-header">
              <span className="sdm-match-file">{name}</span>
              {dir && <span className="sdm-match-path">{dir}/</span>}
            </div>
            <div className="sdm-match-snippet">
              <MarkdownView>{snippet}</MarkdownView>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function KvTable({ data }: { data: Record<string, unknown> }) {
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

function isMarkdownLike(s: string): boolean {
  return /^#{1,6} |\*\*[\w]|^- [\w]|^> |```/.test(s);
}

function FormattedResult({ tool, result, trace, stepIdx }: { tool?: string; result: unknown; trace?: TraceEvent[]; stepIdx?: number }) {
  const resolved = resolveResult(result, tool ?? "", trace, stepIdx);
  const parsed = tryParseJson(resolved);

  if (parsed && isTerminalResult(parsed)) return <TerminalOutput data={parsed} />;
  if (parsed && isHttpResult(parsed)) return <HttpOutput data={parsed} />;

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
    if (items) {
      return (
        <ul className="sdm-file-list">
          {items.map((item, i) => (
            <li key={i} className="sdm-file-item">
              <span className="sdm-file-icon">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="5" y1="3" x2="5" y2="13" />
                  <line x1="11" y1="3" x2="11" y2="13" />
                  <line x1="2.5" y1="6" x2="13.5" y2="6" />
                  <line x1="2.5" y1="10" x2="13.5" y2="10" />
                </svg>
              </span>
              <span className="sdm-file-name">{item}</span>
            </li>
          ))}
        </ul>
      );
    }
  }

  // Vault backlinks — render as link list
  if (tool === "vault_backlinks" && parsed && typeof parsed === "object") {
    const p = parsed as Record<string, unknown>;
    const links = Array.isArray(p.backlinks) ? p.backlinks as string[] : null;
    if (links) {
      if (!links.length) return <div className="sdm-empty">No backlinks</div>;
      return (
        <ul className="sdm-file-list">
          {links.map((link, i) => {
            const name = typeof link === "string" ? link.split("/").pop() ?? link : JSON.stringify(link);
            const dir = typeof link === "string" ? link.split("/").slice(0, -1).join("/") : "";
            return (
              <li key={i} className="sdm-file-item">
                <span className="sdm-file-icon">
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6.5 9.5a3.536 3.536 0 0 0 5 0l2-2a3.536 3.536 0 0 0-5-5L7 4" />
                    <path d="M9.5 6.5a3.536 3.536 0 0 0-5 0l-2 2a3.536 3.536 0 0 0 5 5L9 12" />
                  </svg>
                </span>
                <span className="sdm-file-name">{name}</span>
                {dir && <span className="sdm-file-dir">{dir}/</span>}
              </li>
            );
          })}
        </ul>
      );
    }
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

// ── Modal ──────────────────────────────────────────────────────────────────

interface Props {
  group: CoalescedStep;
  trace?: TraceEvent[];
  onClose: () => void;
}

export default function StepDetailModal({ group, trace, onClose }: Props) {
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === backdropRef.current) onClose();
  }

  let toolStepIdx = 0;

  return (
    <div className="sdm-backdrop" ref={backdropRef} onClick={handleBackdropClick}>
      <div className="sdm-modal">
        <div className="sdm-header">
          {group.type === "tool" ? (
            <>
              <span className="sdm-icon"><ToolIcon tool={group.tool ?? ""} /></span>
              <span className="sdm-title">{metaLabel(group.tool ?? "")}</span>
              {group.steps.length > 1 && (
                <span className="sdm-count">{group.steps.length} calls</span>
              )}
            </>
          ) : (
            <>
              <span className="sdm-icon sdm-icon--text">
                <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6A1.5 1.5 0 0 1 12.5 11H5l-3 3V3.5z" />
                </svg>
              </span>
              <span className="sdm-title">Thinking</span>
            </>
          )}
          <button className="sdm-close" onClick={onClose} type="button" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="3" y1="3" x2="13" y2="13" />
              <line x1="13" y1="3" x2="3" y2="13" />
            </svg>
          </button>
        </div>
        <div className="sdm-body">
          {group.type === "tool" ? (
            <div className="sdm-log">
              {group.steps.map((step, i) => {
                if (step.type === "tool") toolStepIdx++;
                const currentIdx = step.type === "tool" ? toolStepIdx - 1 : undefined;
                return (
                  <div key={step.id} className="sdm-log-entry">
                    <div className="sdm-log-header">
                      {statusDot(step.status)}
                      <span className="sdm-log-num">#{i + 1}</span>
                      <span className="sdm-log-status">{statusText(step.status)}</span>
                    </div>
                    <ToolArgsSummary tool={group.tool} args={step.args} />
                    {(step.result_preview ?? step.result) != null && (
                      <FormattedResult
                        tool={group.tool}
                        result={step.result_preview ?? step.result}
                        trace={trace}
                        stepIdx={currentIdx}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="sdm-text-content">
              {(() => {
                const text = group.steps.map((s) => s.text ?? "").join("").trim();
                return text
                  ? <MarkdownView>{text}</MarkdownView>
                  : <span className="sdm-empty">(empty)</span>;
              })()}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
