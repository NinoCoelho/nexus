/**
 * PlanReviewModal — show the agent's intended steps before executing an op.
 *
 * Displayed for chat-kind operations marked ``preview: true``. While the plan
 * is being generated, shows a spinner + transcript snippet. Once the plan
 * lands, parses the ``nexus-plan`` JSON fence and renders each step as a
 * checklist row, highlighting steps with ``mutates: true``. The user can
 * **Approve** (kicks the real execute), **Refine** (re-runs the plan with
 * a one-line tweak appended to the seed via session follow-up), or
 * **Cancel** (drops the plan session).
 *
 * The plan text is editable in a textarea so the user can tweak before
 * approving — the executed run uses the textarea's contents, not the
 * agent's original output.
 */

import { useCallback, useEffect, useState } from "react";
import type { DashboardOperation } from "../../api/dashboard";
import { getSession } from "../../api/sessions";
import { subscribeSessionEvents } from "../../api/chat";

interface PlanStep {
  action: string;
  target?: string;
  detail?: string;
  mutates?: boolean;
}

interface Props {
  operation: DashboardOperation;
  /** Plan-only session id returned from planOperation(). */
  sessionId: string;
  /** Called when the user approves the plan (possibly edited). Receives the
   *  raw plan JSON string the agent produced (or the user's edits). */
  onApprove: (approvedPlan: string) => void | Promise<void>;
  onCancel: () => void;
}

function extractPlanFence(text: string): { json: string } | null {
  const re = /```nexus-plan\s*\n([\s\S]*?)```/g;
  let lastMatch: RegExpExecArray | null = null;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) lastMatch = m;
  if (!lastMatch) return null;
  return { json: lastMatch[1].trim() };
}

function parseSteps(json: string): { steps: PlanStep[] | null; error: string | null } {
  try {
    const parsed = JSON.parse(json);
    if (!Array.isArray(parsed)) return { steps: null, error: "Plan is not a JSON array" };
    return {
      steps: parsed.map((s) => ({
        action: typeof s.action === "string" ? s.action : "(unknown)",
        target: typeof s.target === "string" ? s.target : undefined,
        detail: typeof s.detail === "string" ? s.detail : undefined,
        mutates: !!s.mutates,
      })),
      error: null,
    };
  } catch (e) {
    return { steps: null, error: (e as Error).message };
  }
}

export default function PlanReviewModal({ operation, sessionId, onApprove, onCancel }: Props) {
  const [planText, setPlanText] = useState<string | null>(null);
  const [editedPlan, setEditedPlan] = useState("");
  const [prose, setProse] = useState("");
  const [status, setStatus] = useState<"running" | "ready" | "failed">("running");
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);

  // Wait for the plan turn to finish, then read the assistant's last message
  // and try to extract a `nexus-plan` fence.
  useEffect(() => {
    let cancelled = false;
    let sub: { close: () => void } | null = null;

    const finish = async () => {
      try {
        const detail = await getSession(sessionId);
        if (cancelled) return;
        const lastAssistant = [...detail.messages]
          .reverse()
          .find((m) => m.role === "assistant");
        const content = lastAssistant?.content ?? "";
        const fence = extractPlanFence(content);
        if (!fence) {
          setStatus("failed");
          setError("Agent didn't return a parseable plan. Try again or refine the prompt.");
          setProse(content);
          return;
        }
        setPlanText(fence.json);
        setEditedPlan(fence.json);
        // Strip the fence from prose so we don't show it twice.
        setProse(content.replace(/```nexus-plan[\s\S]*?```/g, "").trim());
        setStatus("ready");
      } catch (e) {
        if (cancelled) return;
        setStatus("failed");
        setError((e as Error).message);
      }
    };

    sub = subscribeSessionEvents(sessionId, (event) => {
      if (event.kind !== "op_done") return;
      if (event.data.status !== "done") {
        setStatus("failed");
        setError(event.data.error ?? "Plan run failed");
        return;
      }
      void finish();
    });

    return () => {
      cancelled = true;
      sub?.close();
    };
  }, [sessionId]);

  const handleApprove = useCallback(async () => {
    if (committing || status !== "ready") return;
    setCommitting(true);
    try {
      await onApprove(editedPlan.trim() || planText || "[]");
    } finally {
      setCommitting(false);
    }
  }, [committing, status, editedPlan, planText, onApprove]);

  const { steps } = planText ? parseSteps(editedPlan || planText) : { steps: null };

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div
        className="dt-modal plan-review-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ minWidth: 560, maxWidth: 760, maxHeight: "85vh", display: "flex", flexDirection: "column" }}
      >
        <div className="dt-modal-title">Preview: {operation.label}</div>

        {status === "running" && (
          <div className="data-dash-hint" style={{ padding: 16 }}>
            <span className="kanban-card-spin" aria-hidden style={{ marginRight: 8 }} />
            Building a plan… the agent is reading what it needs without making changes.
          </div>
        )}

        {status === "failed" && (
          <div className="dt-error" style={{ padding: 12 }}>
            {error ?? "Plan run failed"}
            {prose && (
              <details style={{ marginTop: 8 }}>
                <summary>Show agent's reply</summary>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: 11.5 }}>{prose}</pre>
              </details>
            )}
          </div>
        )}

        {status === "ready" && (
          <>
            {prose && (
              <div className="data-dash-hint" style={{ marginBottom: 6 }}>{prose}</div>
            )}

            {steps && (
              <div className="plan-review-steps">
                {steps.map((step, i) => (
                  <div
                    key={i}
                    className={`plan-review-step${step.mutates ? " plan-review-step--mutates" : ""}`}
                  >
                    <span className="plan-review-step-num">{i + 1}.</span>
                    <span className="plan-review-step-action">{step.action}</span>
                    {step.target && (
                      <span className="plan-review-step-target">{step.target}</span>
                    )}
                    {step.mutates && (
                      <span className="plan-review-step-tag" title="This step modifies data.">
                        write
                      </span>
                    )}
                    {step.detail && (
                      <div className="plan-review-step-detail">{step.detail}</div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <details className="plan-review-edit">
              <summary>Edit raw plan JSON</summary>
              <textarea
                className="form-input form-textarea"
                value={editedPlan}
                onChange={(e) => setEditedPlan(e.target.value)}
                rows={8}
                style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, marginTop: 6 }}
              />
            </details>
          </>
        )}

        <div className="dt-modal-actions" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="approval-btn"
            onClick={onCancel}
            disabled={committing}
          >
            Cancel
          </button>
          <button
            type="button"
            className="approval-btn approval-btn-allow"
            onClick={() => void handleApprove()}
            disabled={committing || status !== "ready"}
            title={status === "ready" ? "Run the operation against this plan." : "Waiting for plan…"}
          >
            {committing ? "Starting…" : "Approve & run"}
          </button>
        </div>
      </div>
    </div>
  );
}
