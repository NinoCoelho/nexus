// Partial-turn action banner for ChatView.

import type { Message } from "./index";

export const PARTIAL_LABEL: Record<NonNullable<Message["partial"]>["status"], string> = {
  interrupted: "This turn was interrupted (connection dropped or server restarted).",
  cancelled: "You stopped this turn.",
  iteration_limit: "Hit the per-turn tool-call limit.",
  empty_response: "The model returned an empty response.",
  llm_error: "The model call failed mid-turn.",
  crashed: "The turn crashed unexpectedly.",
  length: "Response was truncated — the model hit its output limit.",
  upstream_timeout: "The model didn't respond in time.",
};

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
};

export function PartialTurnActions({
  status,
  onRetry,
  onContinue,
}: {
  status: NonNullable<Message["partial"]>["status"];
  onRetry?: () => void;
  onContinue?: () => void;
}) {
  const showContinue = PARTIAL_CAN_CONTINUE[status] && !!onContinue;
  return (
    <div className="limit-banner" style={{ marginTop: 4 }}>
      <div className="limit-banner-text">{PARTIAL_LABEL[status]} How do you want to proceed?</div>
      <div className="limit-banner-actions">
        {showContinue && (
          <button
            className="limit-banner-btn limit-banner-btn-primary"
            onClick={onContinue}
            type="button"
          >
            Continue
          </button>
        )}
        {onRetry && (
          <button
            className={showContinue ? "limit-banner-btn" : "limit-banner-btn limit-banner-btn-primary"}
            onClick={onRetry}
            type="button"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
