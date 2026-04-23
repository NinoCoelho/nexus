import React, { useEffect, useRef } from "react";
import type { TraceEvent } from "../api";
import type { CoalescedStep } from "./ActivityTimeline";
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

function humanizeArgs(args: unknown): string[] {
  if (!args || typeof args !== "object") return [];
  const entries: string[] = [];
  const obj = args as Record<string, unknown>;
  for (const [key, val] of Object.entries(obj)) {
    if (val == null) continue;
    const display = typeof val === "string"
      ? val
      : typeof val === "object"
      ? JSON.stringify(val)
      : String(val);
    const truncated = display.length > 200 ? display.slice(0, 200) + "..." : display;
    entries.push(`${key}: ${truncated}`);
  }
  return entries;
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
      {data.body && (
        <pre className="sdm-term-output">{data.body.length > 2000 ? data.body.slice(0, 2000) + "\n…" : data.body}</pre>
      )}
    </div>
  );
}

function GenericResult({ raw }: { raw: string }) {
  const parsed = tryParseJson(raw);
  if (parsed && typeof parsed === "object") {
    return (
      <pre className="sdm-result-json">{JSON.stringify(parsed, null, 2)}</pre>
    );
  }
  return (
    <div className="sdm-log-result">
      <span className="sdm-log-arrow">→</span>
      {raw.length > 600 ? raw.slice(0, 600) + "…" : raw}
    </div>
  );
}

function FormattedResult({ tool, result, trace, stepIdx }: { tool?: string; result: unknown; trace?: TraceEvent[]; stepIdx?: number }) {
  const resolved = resolveResult(result, tool ?? "", trace, stepIdx);
  const parsed = tryParseJson(resolved);

  if (parsed && tool === "terminal" && isTerminalResult(parsed)) {
    return <TerminalOutput data={parsed} />;
  }
  if (parsed && tool === "http_call" && isHttpResult(parsed)) {
    return <HttpOutput data={parsed} />;
  }

  const asStr = typeof resolved === "string" ? resolved : resolved != null ? JSON.stringify(resolved) : null;
  if (!asStr) return null;

  if (parsed && typeof parsed === "object") {
    if (isTerminalResult(parsed)) return <TerminalOutput data={parsed} />;
    if (isHttpResult(parsed)) return <HttpOutput data={parsed} />;
  }

  return <GenericResult raw={asStr} />;
}

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
              <span className="sdm-title">Text response</span>
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
                const args = humanizeArgs(step.args);
                if (step.type === "tool") toolStepIdx++;
                const currentIdx = step.type === "tool" ? toolStepIdx - 1 : undefined;
                return (
                  <div key={step.id} className="sdm-log-entry">
                    <div className="sdm-log-header">
                      {statusDot(step.status)}
                      <span className="sdm-log-num">#{i + 1}</span>
                      <span className="sdm-log-status">{statusText(step.status)}</span>
                    </div>
                    {args.length > 0 && (
                      <div className="sdm-log-detail">
                        {args.map((line, j) => (
                          <div key={j} className="sdm-log-kv">{line}</div>
                        ))}
                      </div>
                    )}
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
              {group.steps.map((s) => s.text ?? "").join("").trim() || "(empty)"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
