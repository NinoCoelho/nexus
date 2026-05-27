// Partial-turn action banner for ChatView.

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Message } from "./index";

// Statuses where Continue is useful (i.e. the turn has meaningful content
// to build on). Retry alone for pure-failure states.
export const PARTIAL_CAN_CONTINUE: Record<NonNullable<Message["partial"]>["status"], boolean> = {
  interrupted: true,
  cancelled: true,
  iteration_limit: true,
  empty_response: false,
  llm_error: false,
  crashed: false,
  length: true,
  upstream_timeout: false,
  rate_limited: true,
  context_overflow: false,
  message_too_large: false,
  budget_exceeded: false,
};

const PARTIAL_KEY: Record<NonNullable<Message["partial"]>["status"], string> = {
  interrupted: "chat:partial.interrupted",
  cancelled: "chat:partial.cancelled",
  iteration_limit: "chat:partial.iterationLimit",
  empty_response: "chat:partial.emptyResponse",
  llm_error: "chat:partial.llmError",
  crashed: "chat:partial.crashed",
  length: "chat:partial.length",
  upstream_timeout: "chat:partial.upstreamTimeout",
  rate_limited: "chat:partial.rateLimited",
  context_overflow: "chat:partial.contextOverflow",
  message_too_large: "chat:partial.messageTooLarge",
  budget_exceeded: "chat:partial.budgetExceeded",
};

export function PartialTurnActions({
  status,
  onRetry,
  onContinue,
  onCompact,
  onNewSession,
  onRemoveLast,
  onResumePaused,
  partial,
}: {
  status: NonNullable<Message["partial"]>["status"];
  onRetry?: () => void;
  onContinue?: () => void;
  onCompact?: () => Promise<unknown>;
  onNewSession?: () => void;
  onRemoveLast?: () => void;
  onResumePaused?: () => void;
  partial?: Message["partial"];
}) {
  const { t } = useTranslation("chat");
  const [compacting, setCompacting] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);
  const showContinue = PARTIAL_CAN_CONTINUE[status] && !!onContinue;
  const isContextIssue = status === "context_overflow" || status === "message_too_large";
  const isRateLimited = status === "rate_limited" && partial?.retryAfter;

  useEffect(() => {
    if (!isRateLimited || !partial?.retryAfter) return;
    const target = new Date(partial.retryAfter).getTime();
    if (isNaN(target)) return;
    const update = () => {
      const left = Math.max(0, Math.ceil((target - Date.now()) / 1000));
      setRemaining(left);
    };
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [isRateLimited, partial?.retryAfter]);

  const doCompact = useCallback(() => {
    if (compacting || !onCompact) return;
    setCompacting(true);
    onCompact().finally(() => setCompacting(false));
  }, [compacting, onCompact]);

  const fmtCountdown = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  if (isRateLimited) {
    const countdownDone = remaining !== null && remaining <= 0;
    return (
      <div className="limit-banner" style={{ marginTop: 4 }}>
        <div className="limit-banner-text">
          {t("chat:partial.rateLimited")}
          {remaining !== null && remaining > 0 && (
            <span className="rate-limit-countdown" style={{ marginLeft: 8, fontFamily: "monospace" }}>
              {fmtCountdown(remaining)}
            </span>
          )}
        </div>
        <div className="limit-banner-actions">
          {onResumePaused && (
            <button
              className="limit-banner-btn limit-banner-btn-primary"
              onClick={onResumePaused}
              disabled={!countdownDone}
              type="button"
            >
              {t("chat:partial.resume")}
            </button>
          )}
          {onNewSession && (
            <button
              className="limit-banner-btn"
              onClick={onNewSession}
              type="button"
            >
              {t("chat:partial.newSession")}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="limit-banner" style={{ marginTop: 4 }}>
      <div className="limit-banner-text">{t(PARTIAL_KEY[status])} {t("chat:partial.proceed")}</div>
      <div className="limit-banner-actions">
        {isContextIssue ? (
          <>
            {onCompact && (
              <button
                className="limit-banner-btn limit-banner-btn-primary"
                onClick={doCompact}
                disabled={compacting}
                type="button"
              >
                {compacting ? "Compacting…" : t("chat:partial.compact")}
              </button>
            )}
            {onRemoveLast && (
              <button
                className="limit-banner-btn"
                onClick={onRemoveLast}
                type="button"
              >
                {t("chat:partial.removeLast")}
              </button>
            )}
            {onNewSession && (
              <button
                className="limit-banner-btn"
                onClick={onNewSession}
                type="button"
              >
                {t("chat:partial.newSession")}
              </button>
            )}
          </>
        ) : (
          <>
            {showContinue && (
              <button
                className="limit-banner-btn limit-banner-btn-primary"
                onClick={onContinue}
                type="button"
              >
                {t("chat:partial.continue")}
              </button>
            )}
            {onRetry && (
              <button
                className={showContinue ? "limit-banner-btn" : "limit-banner-btn limit-banner-btn-primary"}
                onClick={onRetry}
                type="button"
              >
                {t("chat:partial.retry")}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
