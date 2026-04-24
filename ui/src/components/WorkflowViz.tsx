/**
 * WorkflowViz — vertical timeline visualization of agent tool calls.
 *
 * Currently unused (not imported by any other component). Renders a
 * step-by-step vertical workflow from the trace events, showing each
 * tool call as a node with connecting lines. An alternative to the
 * ActivityTimeline component.
 */

import type { TraceEvent } from "../api";
import "./WorkflowViz.css";

// ── Planner plan types ────────────────────────────────────────────────────────

export interface PlanSubTask {
  id: string;
  description: string;
  status: "pending" | "running" | "done" | "failed";
  result_preview?: string;
}

interface PlanVizProps {
  subTasks: PlanSubTask[];
}

function SubTaskStatusIcon({ status }: { status: PlanSubTask["status"] }) {
  if (status === "done") {
    return (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="4 10 8 14 16 6" />
      </svg>
    );
  }
  if (status === "failed") {
    return (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="5" y1="5" x2="15" y2="15" />
        <line x1="15" y1="5" x2="5" y2="15" />
      </svg>
    );
  }
  if (status === "running") {
    return (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ animation: "spin 1s linear infinite" }}>
        <circle cx="10" cy="10" r="7" strokeDasharray="22 10" />
      </svg>
    );
  }
  // pending
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="7" />
    </svg>
  );
}

export function PlanViz({ subTasks }: PlanVizProps) {
  if (!subTasks || subTasks.length === 0) return null;

  return (
    <div className="plan-viz">
      <div className="plan-viz__header">Plan</div>
      <ol className="plan-viz__list">
        {subTasks.map((st) => (
          <li
            key={st.id}
            className={`plan-viz__item plan-viz__item--${st.status}`}
          >
            <span className="plan-viz__status-icon">
              <SubTaskStatusIcon status={st.status} />
            </span>
            <span className="plan-viz__description">{st.description}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

interface Props {
  trace: TraceEvent[];
}

interface Stage {
  tool: string;
  label: string;
  icon: React.ReactNode;
}

function toolLabel(tool: string): string {
  const map: Record<string, string> = {
    skill_manage: "Authoring",
    skill_view: "Reading",
    http_call: "Fetching",
    acp_call: "Delegating",
  };
  return map[tool] ?? tool.replace(/_/g, " ");
}

function ToolIcon({ tool }: { tool: string }) {
  if (tool === "skill_manage") {
    return (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M13.5 3.5L16.5 6.5L7 16H4v-3L13.5 3.5z" />
      </svg>
    );
  }
  if (tool === "skill_view") {
    return (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h5v12H4zM11 4h5v12h-5z" />
      </svg>
    );
  }
  if (tool === "http_call") {
    return (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="10" cy="10" r="7" />
        <path d="M3 10h14M10 3c-2 2-3 4.5-3 7s1 5 3 7M10 3c2 2 3 4.5 3 7s-1 5-3 7" />
      </svg>
    );
  }
  if (tool === "acp_call") {
    return (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 6h9M3 10h14M8 14h9" />
        <circle cx="5" cy="6" r="2" />
        <circle cx="15" cy="14" r="2" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="3" />
    </svg>
  );
}

const META_TOOLS = new Set(["skills_list", "skill_view"]);

export default function WorkflowViz({ trace }: Props) {
  const toolEvents = trace.filter((e) => e.tool && !META_TOOLS.has(e.tool));
  if (toolEvents.length < 2) return null;

  // Dedupe consecutive same tools, build stage list
  const stages: Stage[] = [];
  for (const ev of toolEvents) {
    const tool = ev.tool!;
    if (stages.length === 0 || stages[stages.length - 1].tool !== tool) {
      stages.push({
        tool,
        label: toolLabel(tool),
        icon: <ToolIcon tool={tool} />,
      });
    }
  }

  const summary = stages.slice(0, 3).map((s) => s.label).join(" → ");
  const showIconRow = stages.length >= 3;

  return (
    <div className="workflow-viz">
      {/* Progress ribbon */}
      <div className="wf-ribbon">
        <div className="wf-line" />
        {stages.map((stage, i) => (
          <div
            key={i}
            className={`wf-stage ${i === stages.length - 1 ? "wf-stage--current" : "wf-stage--done"}`}
            style={{ left: `${(i / Math.max(stages.length - 1, 1)) * 100}%` }}
          >
            <div className="wf-dot" />
            <span className="wf-stage-label">{stage.label}</span>
          </div>
        ))}
      </div>

      {/* Icon row — only when 3+ stages */}
      {showIconRow && (
        <div className="wf-icon-row">
          {stages.map((stage, i) => (
            <div key={i} className="wf-icon-group">
              <div className="wf-icon">{stage.icon}</div>
              <span className="wf-icon-label">{stage.label.toUpperCase()}</span>
              {i < stages.length - 1 && <span className="wf-arrow">→</span>}
            </div>
          ))}
        </div>
      )}

      <p className="wf-caption">Agentic workflow: {summary}</p>
    </div>
  );
}
