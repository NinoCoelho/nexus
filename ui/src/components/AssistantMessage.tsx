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

  const toolCount = trace
    ? trace.filter((e) => e.tool && !META_TOOLS.has(e.tool)).length
    : 0;
  const showWorkflow = toolCount >= 2;

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
        {trace && trace.length > 0 && (
          <div className="asst-trace-section">
            <button
              className="asst-trace-toggle"
              onClick={() => setTraceOpen((v) => !v)}
            >
              {traceOpen ? "▾" : "▸"} Tool activity ({trace.length})
            </button>
            {traceOpen && (
              <pre className="asst-trace-json">
                {JSON.stringify(trace, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
