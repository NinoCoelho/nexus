// Partial-turn action banner for ChatView.

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
};

export function PartialTurnActions({
  status,
  onRetry,
  onContinue,
  onCompact,
  onNewSession,
  onRemoveLast,
}: {
  status: NonNullable<Message["partial"]>["status"];
  onRetry?: () => void;
  onContinue?: () => void;
  onCompact?: () => void;
  onNewSession?: () => void;
  onRemoveLast?: () => void;
}) {
  const { t } = useTranslation("chat");
  const showContinue = PARTIAL_CAN_CONTINUE[status] && !!onContinue;
  const isContextIssue = status === "context_overflow" || status === "message_too_large";
  return (
    <div className="limit-banner" style={{ marginTop: 4 }}>
      <div className="limit-banner-text">{t(PARTIAL_KEY[status])} {t("chat:partial.proceed")}</div>
      <div className="limit-banner-actions">
        {isContextIssue ? (
          <>
            {onCompact && (
              <button
                className="limit-banner-btn limit-banner-btn-primary"
                onClick={onCompact}
                type="button"
              >
                {t("chat:partial.compact")}
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
