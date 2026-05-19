import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { UserRequestPayload } from "../api";
import FormRenderer from "./FormRenderer";
import { sounds } from "../hooks/useSounds";
import "./ApprovalDialog.css";

interface Props {
  request: UserRequestPayload;
  onSubmit: (answer: string | Record<string, unknown>) => void;
  onTimeout: () => void;
  onCancel?: () => void;
  queueLength?: number;
}

export default function ApprovalDialog({
  request,
  onSubmit,
  onTimeout,
  onCancel,
  queueLength = 1,
}: Props) {
  const { t } = useTranslation("chat");
  const [remaining, setRemaining] = useState(request.timeout_seconds);
  const [textValue, setTextValue] = useState(request.default ?? "");
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState("");

  const showCustom = request.kind !== "text";

  useEffect(() => {
    const id = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          clearInterval(id);
          onTimeout();
          return 0;
        }
        const next = r - 1;
        if (next > 0 && next <= 10) sounds.countdownTick();
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

  function submitCustom(e: FormEvent) {
    e.preventDefault();
    if (!customValue.trim()) return;
    onSubmit(customValue);
  }

  return (
    <div className="approval-backdrop" role="dialog" aria-modal="true">
      <div className="approval-dialog">
        <div className="approval-header">
          <span className="approval-title">
            {request.kind === "form" && request.form_title
              ? request.form_title
              : t("chat:approval.defaultTitle")}
          </span>
          <span className="approval-countdown" title={t("chat:approval.timeoutLabel")}>
            {formatCountdown(remaining)}
          </span>
        </div>
        {queueLength > 1 && (
          <div className="approval-queue-hint" title="Total pending HITL requests">
            {t("chat:approval.queueHint", { total: queueLength })}
          </div>
        )}
        <div className="approval-body">
          <p className="approval-prompt">{request.prompt}</p>
          {request.kind === "form" && request.form_description && (
            <p className="approval-prompt" style={{ color: "var(--fg-faint)", fontSize: "12px" }}>
              {request.form_description}
            </p>
          )}

          {request.kind === "form" && request.fields && (
            <FormRenderer
              fields={request.fields}
              onSubmit={(values) => onSubmit(values)}
              submitLabel="Submit"
            />
          )}
        </div>

        {request.kind === "confirm" && (
          <div className="approval-buttons">
            <button
              type="button"
              className="approval-btn approval-btn-deny"
              onClick={() => onSubmit("no")}
            >
              {t("chat:approval.deny")}
            </button>
            <button
              type="button"
              className="approval-btn approval-btn-allow"
              onClick={() => onSubmit("yes")}
              autoFocus
            >
              {t("chat:approval.allow")}
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
              placeholder={request.default ?? t("chat:approval.answerPlaceholder")}
              autoFocus
            />
            <button
              type="submit"
              className="approval-btn approval-btn-allow"
              disabled={!textValue.trim()}
            >
              {t("chat:approval.send")}
            </button>
          </form>
        )}

        <div className="approval-footer">
          <div className="approval-footer-left">
            {onCancel && (
              <button
                type="button"
                className="approval-btn approval-btn-cancel"
                onClick={onCancel}
              >
                {t("chat:approval.cancel")}
              </button>
            )}
          </div>
          {showCustom && (
            <button
              type="button"
              className="approval-custom-toggle"
              onClick={() => setCustomOpen((v) => !v)}
            >
              {t("chat:approval.customResponse")}
            </button>
          )}
        </div>

        {showCustom && customOpen && (
          <form onSubmit={submitCustom} className="approval-text-row approval-custom-row">
            <input
              className="approval-text-input"
              value={customValue}
              onChange={(e) => setCustomValue(e.target.value)}
              placeholder={t("chat:approval.customPlaceholder")}
              autoFocus
            />
            <button
              type="submit"
              className="approval-btn approval-btn-allow"
              disabled={!customValue.trim()}
            >
              {t("chat:approval.customSend")}
            </button>
          </form>
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
