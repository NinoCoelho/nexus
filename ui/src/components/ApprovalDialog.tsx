// Generic dialog for every ask_user HITL prompt — confirm / choice /
// text. Closes when the user picks an answer (POST /respond) or when
// the timeout elapses (server is the source of truth on timeout, but
// the UI shows a live countdown so the pause doesn't feel frozen).
//
// Rule from project: never use native alert/prompt/confirm. This IS
// the replacement.

import { FormEvent, useEffect, useState } from "react";
import type { UserRequestPayload } from "../api";
import FormRenderer from "./FormRenderer";
import { sounds } from "../hooks/useSounds";
import "./ApprovalDialog.css";

interface Props {
  request: UserRequestPayload;
  onSubmit: (answer: string | Record<string, unknown>) => void;
  onTimeout: () => void;
  /** Total pending including this one — when > 1 a "1 of N" hint shows. */
  queueLength?: number;
}

export default function ApprovalDialog({ request, onSubmit, onTimeout, queueLength = 1 }: Props) {
  const [remaining, setRemaining] = useState(request.timeout_seconds);
  const [textValue, setTextValue] = useState(request.default ?? "");

  useEffect(() => {
    // Simple 1s countdown. Server holds the authoritative timeout; the
    // UI's job is just to convey urgency.
    const id = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          clearInterval(id);
          onTimeout();
          return 0;
        }
        const next = r - 1;
        // Per-second tick during the final 10s ramp-down.
        if (next > 0 && next <= 10) sounds.countdownTick();
        // Every minute of waiting, nudge the user with an attention chime.
        else if (next > 0 && next % 60 === 0) sounds.attention();
        return next;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [onTimeout]);

  function submitText(e: FormEvent) {
    e.preventDefault();
    if (!textValue.trim()) return;
    onSubmit(textValue);
  }

  return (
    <div className="approval-backdrop" role="dialog" aria-modal="true">
      <div className="approval-dialog">
        <div className="approval-header">
          <span className="approval-title">
            {request.kind === "form" && request.form_title
              ? request.form_title
              : "Agent needs input"}
          </span>
          <span className="approval-countdown" title="Timeout">
            {formatCountdown(remaining)}
          </span>
        </div>
        {queueLength > 1 && (
          <div className="approval-queue-hint" title="Total pending HITL requests">
            1 of {queueLength} pending
          </div>
        )}
        <p className="approval-prompt">{request.prompt}</p>
        {request.kind === "form" && request.form_description && (
          <p className="approval-prompt" style={{ color: "var(--fg-faint)", fontSize: "12px" }}>
            {request.form_description}
          </p>
        )}

        {request.kind === "confirm" && (
          <div className="approval-buttons">
            <button
              type="button"
              className="approval-btn approval-btn-deny"
              onClick={() => onSubmit("no")}
            >
              Deny
            </button>
            <button
              type="button"
              className="approval-btn approval-btn-allow"
              onClick={() => onSubmit("yes")}
              autoFocus
            >
              Allow
            </button>
          </div>
        )}

        {request.kind === "choice" && request.choices && (
          <div className="approval-buttons approval-buttons-choice">
            {request.choices.map((choice) => (
              <button
                type="button"
                key={choice}
                className={
                  "approval-btn" +
                  (choice === request.default ? " approval-btn-allow" : "")
                }
                onClick={() => onSubmit(choice)}
              >
                {choice}
              </button>
            ))}
          </div>
        )}

        {request.kind === "text" && (
          <form onSubmit={submitText} className="approval-text-row">
            <input
              className="approval-text-input"
              value={textValue}
              onChange={(e) => setTextValue(e.target.value)}
              placeholder={request.default ?? "Type your answer…"}
              autoFocus
            />
            <button
              type="submit"
              className="approval-btn approval-btn-allow"
              disabled={!textValue.trim()}
            >
              Send
            </button>
          </form>
        )}

        {request.kind === "form" && request.fields && (
          <FormRenderer
            fields={request.fields}
            onSubmit={(values) => onSubmit(values)}
            submitLabel="Submit"
          />
        )}
      </div>
    </div>
  );
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}
