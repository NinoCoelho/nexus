import React, { useEffect, useRef } from "react";
import type { CoalescedStep } from "./ActivityTimeline";
import "./StepDetailModal.css";

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

function resultPreview(val: unknown): string | null {
  if (val == null) return null;
  const s = typeof val === "string" ? val : JSON.stringify(val);
  return s.length > 400 ? s.slice(0, 400) + "..." : s;
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

interface Props {
  group: CoalescedStep;
  onClose: () => void;
}

export default function StepDetailModal({ group, onClose }: Props) {
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
                const res = resultPreview(step.result_preview ?? step.result);
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
                    {res != null && (
                      <div className="sdm-log-result">
                        <span className="sdm-log-arrow">→</span>
                        {res}
                      </div>
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
