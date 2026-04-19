import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { TraceEvent } from "../api";
import WorkflowViz from "./WorkflowViz";
import "./AssistantMessage.css";

interface Props {
  content: string;
  trace?: TraceEvent[];
  timestamp: Date;
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const META_TOOLS = new Set(["skills_list", "skill_view"]);

export default function AssistantMessage({ content, trace, timestamp }: Props) {
  const [traceOpen, setTraceOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const toolCount = trace
    ? trace.filter((e) => e.tool && !META_TOOLS.has(e.tool)).length
    : 0;
  const showWorkflow = toolCount >= 2;

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // clipboard may be blocked; fall through silently
    }
  }

  return (
    <div className="asst-msg">
      <div className="asst-header">
        <div className="asst-avatar" aria-hidden="true" />
        <span className="asst-name">Nexus</span>
        <span className="asst-time">{fmt(timestamp)}</span>
      </div>
      <div className="asst-card">
        <div className="asst-body">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
        {showWorkflow && trace && <WorkflowViz trace={trace} />}
        <div className="asst-footer">
          <button
            className="bubble-action-btn"
            onClick={handleCopy}
            title={copied ? "Copied" : "Copy markdown"}
            aria-label="Copy markdown"
          >
            {copied ? (
              <>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 8 7 12 13 4" />
                </svg>
                <span>Copied</span>
              </>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="5" width="8" height="9" rx="1.5" />
                  <path d="M3 10V3a1 1 0 0 1 1-1h7" />
                </svg>
                <span>Copy</span>
              </>
            )}
          </button>
          {trace && trace.length > 0 && (
            <button
              className="asst-trace-toggle"
              onClick={() => setTraceOpen((v) => !v)}
            >
              {traceOpen ? "▾" : "▸"} Tool activity ({trace.length})
            </button>
          )}
        </div>
        {traceOpen && trace && (
          <pre className="asst-trace-json">
            {JSON.stringify(trace, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
