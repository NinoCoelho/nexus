/**
 * ActivityTimeline — collapsible step-by-step view of what the agent did
 * during a single turn. Each step is either a tool call (with args + result)
 * or a text delta. Steps are coalesced so repeated calls to the same tool
 * show as a single expandable group.
 *
 * Clicking a step opens StepDetailModal for the full args/result view.
 */

import React, { useState } from "react";
import type { TraceEvent } from "../api";
import type { TimelineStep } from "./ChatView";
import StepDetailModal from "./StepDetailModal";
import "./ActivityTimeline.css";

const SKIP_TOOLS = new Set(["_meta", "iter", "reply"]);

function metaFor(tool: string): { label: string; icon: React.ReactElement } {
  switch (tool) {
    case "vault_list":
      return { label: "Listing vault", icon: <IconFolder /> };
    case "vault_read":
      return { label: "Reading", icon: <IconFile /> };
    case "vault_write":
      return { label: "Writing", icon: <IconPencil /> };
    case "vault_search":
      return { label: "Searching vault", icon: <IconMagnifier /> };
    case "vault_tags":
      return { label: "Tags", icon: <IconHash /> };
    case "vault_backlinks":
      return { label: "Backlinks", icon: <IconLink /> };
    case "kanban_manage":
      return { label: "Kanban", icon: <IconKanban /> };
    case "http_call":
      return { label: "HTTP", icon: <IconGlobe /> };
    case "terminal":
      return { label: "Terminal", icon: <IconTerminal /> };
    case "skill_manage":
      return { label: "Authoring skill", icon: <IconPencil /> };
    case "skill_view":
      return { label: "Reading skill", icon: <IconBook /> };
    case "skills_list":
      return { label: "Listing skills", icon: <IconList /> };
    default:
      return { label: tool.replace(/_/g, " "), icon: <IconDot /> };
  }
}

function subtitleFor(tool: string, args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  if (tool.startsWith("vault_")) {
    const p = a.path ?? a.query ?? a.tag ?? "";
    return typeof p === "string" ? p.split("/").pop() ?? "" : "";
  }
  if (tool === "http_call") {
    const url = typeof a.url === "string" ? a.url : "";
    try { return new URL(url).hostname; } catch { return url.slice(0, 24); }
  }
  if (tool === "terminal") {
    const cmd = typeof a.command === "string" ? a.command : "";
    return cmd.length > 40 ? cmd.slice(0, 40) + "…" : cmd;
  }
  if (tool === "kanban_manage") return typeof a.action === "string" ? a.action : "";
  if (tool === "skill_view") return typeof a.name === "string" ? a.name : "";
  return "";
}

const IconFolder = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5H12A1.5 1.5 0 0 1 13.5 6v5A1.5 1.5 0 0 1 12 12.5H4A1.5 1.5 0 0 1 2.5 11z" />
  </svg>
);
const IconFile = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
    <polyline points="9 1.5 9 5 12 5" />
  </svg>
);
const IconPencil = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2z" />
  </svg>
);
const IconMagnifier = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="6.5" cy="6.5" r="4" />
    <line x1="9.5" y1="9.5" x2="13" y2="13" />
  </svg>
);
const IconHash = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="5" y1="3" x2="5" y2="13" />
    <line x1="11" y1="3" x2="11" y2="13" />
    <line x1="2.5" y1="6" x2="13.5" y2="6" />
    <line x1="2.5" y1="10" x2="13.5" y2="10" />
  </svg>
);
const IconLink = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6.5 9.5a3.536 3.536 0 0 0 5 0l2-2a3.536 3.536 0 0 0-5-5L7 4" />
    <path d="M9.5 6.5a3.536 3.536 0 0 0-5 0l-2 2a3.536 3.536 0 0 0 5 5L9 12" />
  </svg>
);
const IconKanban = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="3" height="8" rx="0.5" />
    <rect x="6.5" y="3" width="3" height="5" rx="0.5" />
    <rect x="11" y="3" width="3" height="10" rx="0.5" />
  </svg>
);
const IconGlobe = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="6" />
    <path d="M2 8h12M8 2a9 9 0 0 1 0 12M8 2a9 9 0 0 0 0 12" />
  </svg>
);
const IconTerminal = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="2 4 6 8 2 12" />
    <line x1="8" y1="12" x2="14" y2="12" />
  </svg>
);
const IconBook = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 2.5A1.5 1.5 0 0 1 4.5 1H12v13H4.5A1.5 1.5 0 0 1 3 12.5z" />
    <line x1="3" y1="12.5" x2="12" y2="12.5" />
  </svg>
);
const IconList = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="5" y1="5" x2="13" y2="5" />
    <line x1="5" y1="8" x2="13" y2="8" />
    <line x1="5" y1="11" x2="13" y2="11" />
    <circle cx="2.5" cy="5" r="0.75" fill="currentColor" />
    <circle cx="2.5" cy="8" r="0.75" fill="currentColor" />
    <circle cx="2.5" cy="11" r="0.75" fill="currentColor" />
  </svg>
);
const IconDot = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
    <circle cx="8" cy="8" r="2.5" fill="currentColor" />
  </svg>
);

const IconTextBubble = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6A1.5 1.5 0 0 1 12.5 11H5l-3 3V3.5z" />
  </svg>
);

export interface CoalescedStep {
  steps: TimelineStep[];
  type: "tool" | "text";
  tool?: string;
  args?: unknown;
  status?: "pending" | "done" | "error";
  sub: string;
}

function coalesce(steps: TimelineStep[]): CoalescedStep[] {
  const result: CoalescedStep[] = [];
  const filtered = steps.filter((s) => {
    if (s.type === "text") return true;
    return s.tool != null && !SKIP_TOOLS.has(s.tool);
  });
  for (const step of filtered) {
    const last = result[result.length - 1];
    if (step.type === "tool" && last?.type === "tool" && last.tool === step.tool) {
      const lastSub = subtitleFor(last.tool ?? "", last.args);
      const curSub = subtitleFor(step.tool ?? "", step.args);
      if (lastSub === curSub) {
        last.steps.push(step);
        if (step.status === "pending") last.status = "pending";
        continue;
      }
    }
    result.push({
      steps: [step],
      type: step.type,
      tool: step.tool,
      args: step.args,
      status: step.status,
      sub: step.type === "tool" ? subtitleFor(step.tool ?? "", step.args) : "",
    });
  }
  return result;
}

interface Props {
  steps?: TimelineStep[];
  trace?: TraceEvent[];
  streaming: boolean;
}

export default function ActivityTimeline({ steps, trace, streaming }: Props) {
  const [activeGroup, setActiveGroup] = useState<CoalescedStep | null>(null);

  if (!steps || steps.length === 0) return null;

  const groups = coalesce(steps);
  const visibleGroups = groups.filter((g) => {
    if (g.type === "text") {
      const hasContent = g.steps.some((s) => (s.text ?? "").trim().length > 0);
      return hasContent;
    }
    return true;
  });

  if (visibleGroups.length === 0) return null;

  const lastPendingIdx = visibleGroups.reduce((acc, g, i) => {
    if (g.type === "tool" && g.status === "pending") return i;
    return acc;
  }, -1);

  return (
    <>
      <div className="at-timeline">
        {visibleGroups.map((group, idx) => {
          const isLast = idx === visibleGroups.length - 1;
          const isPulsing = streaming && (isLast || idx === lastPendingIdx);
          const count = group.steps.length;

          if (group.type === "text") {
            const textPreview = group.steps.map((s) => s.text ?? "").join("").trim();
            const preview = textPreview.length > 30 ? textPreview.slice(0, 30) + "..." : textPreview;
            return (
              <React.Fragment key={`g-${idx}`}>
                {idx > 0 && <div className="at-line" />}
                <button
                  className="at-badge at-badge--text"
                  onClick={() => setActiveGroup(group)}
                  title={preview || "Text response"}
                  type="button"
                >
                  <span className="at-badge-icon at-badge-icon--text">
                    <IconTextBubble />
                  </span>
                </button>
              </React.Fragment>
            );
          }

          const { label, icon } = metaFor(group.tool ?? "");
          const statusClass = isPulsing
            ? "at-badge--pulsing"
            : group.status === "done"
            ? "at-badge--done"
            : group.status === "error"
            ? "at-badge--error"
            : "at-badge--idle";

          const tooltip = count > 1
            ? `${label} ×${count}${group.sub ? ` · ${group.sub}` : ""}`
            : `${label}${group.sub ? ` · ${group.sub}` : ""}`;

          return (
            <React.Fragment key={`g-${idx}`}>
              {idx > 0 && <div className="at-line" />}
              <button
                className={`at-badge ${statusClass}`}
                onClick={() => setActiveGroup(group)}
                title={tooltip}
                type="button"
              >
                <span className="at-badge-icon">{icon}</span>
                {count > 1 && <span className="at-badge-count">×{count}</span>}
              </button>
            </React.Fragment>
          );
        })}
      </div>

      {activeGroup && (
        <StepDetailModal
          group={activeGroup}
          trace={trace}
          onClose={() => setActiveGroup(null)}
        />
      )}
    </>
  );
}
