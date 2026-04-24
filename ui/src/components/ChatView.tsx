import { useEffect, useRef } from "react";
import type { TraceEvent } from "../api";
import AssistantMessage from "./AssistantMessage";
import InputBar from "./InputBar";
import "./ChatView.css";

export interface TimelineStep {
  id: string;
  type: "tool" | "text";
  tool?: string;
  args?: unknown;
  result?: unknown;
  result_preview?: string;
  status?: "pending" | "done" | "error";
  text?: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  trace?: TraceEvent[];
  timeline?: TimelineStep[];
  timestamp: Date;
  streaming?: boolean;
  kind?: "limit";
  limitIterations?: number;
  attachments?: { name: string; vaultPath: string }[];
  model?: string;
  routedBy?: "user" | "auto";
  /** Set when the turn didn't reach ``done`` — drives the Retry/Continue action row. */
  partial?: {
    status:
      | "interrupted"
      | "cancelled"
      | "iteration_limit"
      | "empty_response"
      | "llm_error"
      | "crashed"
      | "length"
      | "upstream_timeout";
    detail?: string;
  };
}

/**
 * Stateless ChatView. All chat state (messages, input, thinking) is owned by
 * App and keyed by session id — this way, switching sessions or views never
 * drops an in-flight "thinking" indicator or partially-entered input.
 */
interface Props {
  messages: Message[];
  thinking: boolean;
  input: string;
  onInputChange: (v: string) => void;
  onSend: () => void;
  onStop?: () => void;
  onContinue?: () => void;
  onDismissLimit?: () => void;
  onRetryPartial?: (msgIndex: number) => void;
  onContinuePartial?: (msgIndex: number) => void;
  hasModel: boolean | null;
  onOpenSettings: () => void;
  onOpenInVault?: (path: string) => void;
  attachments?: { name: string; vaultPath: string }[];
  onAttachmentsChange?: (files: { name: string; vaultPath: string }[]) => void;
  onRollback?: (msgIndex: number) => void;
  models?: string[];
  selectedModel?: string;
  onModelChange?: (model: string) => void;
  routingMode?: "fixed" | "auto";
  onRoutingModeChange?: (mode: "fixed" | "auto") => void;
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const PARTIAL_LABEL: Record<NonNullable<Message["partial"]>["status"], string> = {
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
const PARTIAL_CAN_CONTINUE: Record<NonNullable<Message["partial"]>["status"], boolean> = {
  interrupted: true,
  cancelled: true,
  iteration_limit: true,
  empty_response: false,
  llm_error: false,
  crashed: false,
  length: true,
  upstream_timeout: false,
};

function PartialTurnActions({
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

export default function ChatView({
  messages,
  thinking,
  input,
  onInputChange,
  onSend,
  onStop,
  onContinue,
  onDismissLimit,
  onRetryPartial,
  onContinuePartial,
  hasModel,
  onOpenSettings,
  onOpenInVault,
  attachments,
  onAttachmentsChange,
  onRollback,
  models,
  selectedModel,
  onModelChange,
  routingMode,
  onRoutingModeChange,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking]);

  // During streaming, the last message may be an in-progress assistant message
  // with partial content. Show it inline; only show the dots indicator when
  // the assistant hasn't emitted any text yet.
  const lastMsg = messages[messages.length - 1];
  const streamingInProgress = thinking && lastMsg?.role === "assistant" && ((lastMsg.content ?? "").length > 0 || (lastMsg.timeline ?? []).length > 0);
  const visible = messages.filter(
    (m) =>
      (m.content ?? "").trim().length > 0 ||
      m.kind === "limit" ||
      (m.timeline ?? []).length > 0 ||
      m.partial != null,
  );

  return (
    <div className="chat-view">
      <div className="message-list">
        {visible.length === 0 && !thinking && hasModel === false && (
          <div className="chat-empty chat-empty--setup">
            <p className="chat-empty-title">No model configured</p>
            <p className="chat-empty-sub">
              Add a model from one of your configured providers to start chatting.
            </p>
            <button className="settings-btn settings-btn--primary" onClick={onOpenSettings} type="button">
              Open settings
            </button>
          </div>
        )}
        {visible.length === 0 && !thinking && hasModel === true && (
          <div className="chat-empty">
            <p>Start a conversation with Nexus.</p>
          </div>
        )}
        {visible.map((msg, idx) =>
          msg.role === "assistant" ? (
            msg.kind === "limit" ? (
              <div key={idx} className="limit-banner">
                <div className="limit-banner-text">
                  Hit the per-turn tool-call limit ({msg.limitIterations ?? 16}). How do you want to proceed?
                </div>
                <div className="limit-banner-actions">
                  <button
                    className="limit-banner-btn limit-banner-btn-primary"
                    onClick={onContinue}
                    type="button"
                  >
                    Continue
                  </button>
                  <button
                    className="limit-banner-btn"
                    onClick={onDismissLimit}
                    type="button"
                  >
                    Stop
                  </button>
                </div>
              </div>
            ) : (
              <div key={idx}>
                {((msg.content ?? "").length > 0 || (msg.timeline ?? []).length > 0) && (
                  <AssistantMessage
                    content={msg.content}
                    trace={msg.trace}
                    timeline={msg.timeline}
                    timestamp={msg.timestamp}
                    streaming={msg.streaming}
                    onOpenInVault={onOpenInVault}
                    model={msg.model}
                    routedBy={msg.routedBy}
                  />
                )}
                {msg.partial && !thinking && (
                  <PartialTurnActions
                    status={msg.partial.status}
                    onRetry={onRetryPartial ? () => onRetryPartial(idx) : undefined}
                    onContinue={onContinuePartial ? () => onContinuePartial(idx) : undefined}
                  />
                )}
              </div>
            )
          ) : (
            <div key={idx} className="user-msg">
              <div className="user-msg-meta">
                <span className="user-msg-label">You</span>
                <span className="user-msg-time">{fmt(msg.timestamp)}</span>
                {!thinking && idx < visible.length - 1 && onRollback && (
                  <button
                    className="user-msg-rollback"
                    onClick={() => onRollback(idx)}
                    type="button"
                    title="Delete from here and retry"
                  >
                    <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="1,4 1,10 7,10" />
                      <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                    </svg>
                  </button>
                )}
              </div>
              <div className="user-msg-bubble">{msg.content}</div>
            </div>
          )
        )}
        {thinking && !streamingInProgress && (
          <div className="asst-msg">
            <div className="asst-header">
              <div className="asst-avatar" aria-hidden="true" />
              <span className="asst-name">Nexus</span>
            </div>
            <div className="asst-card thinking">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="bottom-region">
        <div className="bottom-inner">
          <div className="input-stack">
            <InputBar
              value={input}
              onChange={onInputChange}
              onSend={onSend}
              disabled={thinking}
              busy={thinking}
              onStop={onStop}
              attachments={attachments}
              onAttachmentsChange={onAttachmentsChange}
              models={models}
              selectedModel={selectedModel}
              onModelChange={onModelChange}
              routingMode={routingMode}
              onRoutingModeChange={onRoutingModeChange}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
