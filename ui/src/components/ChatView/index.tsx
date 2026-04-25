/**
 * @file Main chat interface component for Nexus.
 *
 * Exports `ChatView` (presentational/stateless) and the message history data types
 * (`Message`, `TimelineStep`). All chat state is managed externally by `App` via
 * `useChatSession`, so switching sessions or navigating between views never
 * discards in-progress messages.
 */
import { useEffect, useRef } from "react";
import type { TraceEvent } from "../../api";
import AssistantMessage from "../AssistantMessage";
import InputBar from "../InputBar";
import { PartialTurnActions } from "./partialTurn";
import "../ChatView.css";

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
 * Stateless chat component. All state (messages, input, thinking indicator)
 * is owned by `App` and indexed by `session_id` — switching sessions or views
 * never discards an in-progress response or the text being typed.
 *
 * @param messages - Message list for the active session (user and assistant).
 * @param thinking - `true` while the agent is processing a response.
 * @param input - Current text in the input bar.
 * @param onInputChange - Callback to sync input changes with external state.
 * @param onSend - Trigger a send; accepts an override text for retries/continuations.
 * @param onStop - Cancel the in-progress stream (Stop button).
 * @param onContinue - Continue after the iteration-limit banner.
 * @param onDismissLimit - Dismiss the limit banner without continuing.
 * @param onRetryPartial - Re-send the partial turn at the given visible index.
 * @param onContinuePartial - Continue in-place the partial turn at the given index.
 * @param hasModel - `true` if a model is configured, `false` if not, `null` while loading.
 * @param onOpenSettings - Open the settings panel (used when no model is configured).
 * @param onOpenInVault - Navigate to a vault file by path (`vault://` links).
 * @param attachments - Vault files attached to the next send.
 * @param onAttachmentsChange - Callback to update the attachment list.
 * @param onRollback - Remove messages from the given index and restore text to the input.
 * @param models - Available models for the selector.
 * @param selectedModel - Currently selected model.
 * @param onModelChange - Callback when the model selector changes.
 */
interface Props {
  messages: Message[];
  thinking: boolean;
  input: string;
  onInputChange: (v: string) => void;
  onSend: (overrideText?: string) => void;
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
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
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
            />
          </div>
        </div>
      </div>
    </div>
  );
}
