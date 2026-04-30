/**
 * StepDetailModal — expanded view for a single tool-call or text step
 * in the assistant's activity timeline.
 *
 * Renders rich output for known tool types:
 *   - `terminal` → exit code, stdout/stderr panes, duration badge
 *   - `http_call` → status code + body preview
 *   - Everything else → generic JSON/text result
 *
 * Each step shows its arguments (key-value pairs, truncated at 200 chars)
 * and result (parsed from JSON when possible).
 */

import React, { useEffect, useRef } from "react";
import type { TraceEvent } from "../../api";
import type { CoalescedStep } from "../ActivityTimeline";
import MarkdownView from "../MarkdownView";
import { useVaultLinkPreview, VaultLinkPreviewProvider } from "../vaultLink";
import ToolIcon from "./ToolIcon";
import ToolArgsSummary from "./ToolArgsSummary";
import { FormattedResult } from "./ResultRenderers";
import { metaLabel } from "./types";
import "../StepDetailModal.css";

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
  trace?: TraceEvent[];
  onClose: () => void;
}

export default function StepDetailModal({ group, trace, onClose }: Props) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const { onPreview, modal } = useVaultLinkPreview();

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
    <VaultLinkPreviewProvider onPreview={onPreview}>
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
                  ? <MarkdownView onVaultLinkPreview={onPreview} linkifyVaultPaths>{text}</MarkdownView>
                  : <span className="sdm-empty">(empty)</span>;
              })()}
            </div>
          )}
        </div>
      </div>
      {modal}
    </div>
    </VaultLinkPreviewProvider>
  );
}
