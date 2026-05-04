/**
 * @file Main chat interface component for Nexus.
 *
 * Exports `ChatView` (presentational/stateless) and the message history data types
 * (`Message`, `TimelineStep`). All chat state is managed externally by `App` via
 * `useChatSession`, so switching sessions or navigating between views never
 * discards in-progress messages.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TraceEvent } from "../../api";
import { classify } from "../../fileTypes";
import AssistantMessage from "../AssistantMessage";
import InputBar from "../InputBar";
import { PartialTurnActions } from "./partialTurn";
import ChatSearchBar from "./ChatSearchBar";
import VoiceAttachment from "./VoiceAttachment";
import "../ChatView.css";

// Voice memos are recorded as audio/webm and stored under ``uploads/voice/``.
// ``classify`` keys off the extension and treats ``.webm`` as video, so we
// special-case the voice path to render an <audio> player instead.
function isAudioAttachment(path: string): boolean {
  if (classify(path).kind === "audio") return true;
  return path.startsWith("uploads/voice/") && path.toLowerCase().endsWith(".webm");
}

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
  attachments?: { name: string; vaultPath: string }[];
  model?: string;
  /** Backend-assigned position in session.history; only set for messages
   * loaded from the server. New in-flight turns get a seq after reload. */
  seq?: number;
  /** Persisted thumbs feedback for assistant turns. */
  feedback?: "up" | "down" | null;
  /** Persisted pin flag — survives page reload, listed in the sidebar. */
  pinned?: boolean;
  /** Chain-of-thought reasoning streamed via the `thinking` SSE channel from
   * reasoning models (DeepSeek-R1, GLM thinking, …). Rendered as a collapsed
   * section so it doesn't pollute the visible reply. */
  thinking?: string;
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
  /** Transient hint while the agent backs off after a retryable mid-stream
   * error. Cleared on the next delta/tool event (recovery succeeded) or
   * when the error finally surfaces (recovery exhausted). */
  reconnecting?: {
    attempt: number;
    maxAttempts: number;
    delaySeconds: number;
    reason: string;
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
  searchOpen?: boolean;
  onSearchClose?: () => void;
  input: string;
  onInputChange: (v: string) => void;
  onSend: (override?: string | { text?: string; inPlace?: boolean; bypassSecretGuard?: boolean }) => void;
  onStop?: () => void;
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
  activeSessionId?: string | null;
  onFeedbackChange?: (msgIndex: number, value: "up" | "down" | null) => void;
  onPinChange?: (msgIndex: number, pinned: boolean) => void;
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function ChatView({
  messages,
  thinking,
  searchOpen,
  onSearchClose,
  input,
  onInputChange,
  onSend,
  onStop,
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
  activeSessionId,
  onFeedbackChange,
  onPinChange,
}: Props) {
  const { t } = useTranslation(["chat", "common"]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const messageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);

  useEffect(() => {
    if (autoScrollEnabled) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, thinking, autoScrollEnabled]);

  useEffect(() => {
    if (!searchOpen) setAutoScrollEnabled(true);
  }, [searchOpen]);

  const handleJumpTo = useCallback((idx: number) => {
    setAutoScrollEnabled(false);
    const el = messageRefs.current.get(idx);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.remove("chat-search-highlight");
      // force reflow so animation restarts
      void el.offsetWidth;
      el.classList.add("chat-search-highlight");
    }
  }, []);

  // During streaming, the last message may be an in-progress assistant message
  // with partial content. Show it inline; only show the dots indicator when
  // the assistant hasn't emitted any text yet.
  const lastMsg = messages[messages.length - 1];
  const streamingInProgress = thinking && lastMsg?.role === "assistant" && ((lastMsg.content ?? "").length > 0 || (lastMsg.timeline ?? []).length > 0);
  const visible = messages.filter(
    (m) =>
      (m.content ?? "").trim().length > 0 ||
      (m.timeline ?? []).length > 0 ||
      m.partial != null,
  );

  const setMsgRef = (idx: number) => (el: HTMLDivElement | null) => {
    if (el) messageRefs.current.set(idx, el);
    else messageRefs.current.delete(idx);
  };

  return (
    <div className="chat-view">
      <ChatSearchBar
        open={!!searchOpen}
        messages={visible}
        onClose={() => onSearchClose?.()}
        onJumpTo={handleJumpTo}
      />
      <div className="message-list">
        {visible.length === 0 && !thinking && hasModel === false && (
          <div className="chat-empty chat-empty--setup">
            <p className="chat-empty-title">{t("chat:empty.noModel.title")}</p>
            <p className="chat-empty-sub">
              {t("chat:empty.noModel.sub")}
            </p>
            <button className="settings-btn settings-btn--primary" onClick={onOpenSettings} type="button">
              {t("chat:empty.noModel.openSettings")}
            </button>
          </div>
        )}
        {visible.length === 0 && !thinking && hasModel === true && (
          <div className="chat-empty">
            <p>{t("chat:empty.start")}</p>
          </div>
        )}
        {visible.map((msg, idx) =>
          msg.role === "assistant" ? (
            <div key={idx} ref={setMsgRef(idx)}>
              {((msg.content ?? "").length > 0 || (msg.timeline ?? []).length > 0 || (msg.thinking ?? "").length > 0 || msg.reconnecting != null) && (
                <AssistantMessage
                  content={msg.content}
                  trace={msg.trace}
                  timeline={msg.timeline}
                  thinking={msg.thinking}
                  timestamp={msg.timestamp}
                  streaming={msg.streaming}
                  onOpenInVault={onOpenInVault}
                  model={msg.model}
                  sessionId={activeSessionId ?? null}
                  seq={msg.seq}
                  feedback={msg.feedback ?? null}
                  pinned={msg.pinned ?? false}
                  reconnecting={msg.reconnecting}
                  onFeedbackChange={
                    onFeedbackChange ? (v) => onFeedbackChange(idx, v) : undefined
                  }
                  onPinChange={
                    onPinChange ? (p) => onPinChange(idx, p) : undefined
                  }
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
          ) : (
            <div key={idx} ref={setMsgRef(idx)} className="user-msg">
              <div className="user-msg-meta">
                <span className="user-msg-label">{t("chat:user.label")}</span>
                <span className="user-msg-time">{fmt(msg.timestamp)}</span>
                {!thinking && onRollback && (
                  <button
                    className="user-msg-edit"
                    onClick={() => onRollback(idx)}
                    type="button"
                    title={t("chat:user.editTitle")}
                    aria-label={t("chat:user.editAria")}
                  >
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M11.5 2.5l2 2-7.5 7.5-2.5.5.5-2.5z" />
                      <path d="M10 4l2 2" />
                    </svg>
                  </button>
                )}
              </div>
              <div className="user-msg-bubble">
                {msg.attachments && msg.attachments.length > 0 && (
                  <div className="user-msg-attachments">
                    {msg.attachments.map((a, i) => (
                      isAudioAttachment(a.vaultPath) ? (
                        <VoiceAttachment key={i} path={a.vaultPath} />
                      ) : (
                        <span key={i} className="user-msg-attachment-chip">
                          {a.name}
                        </span>
                      )
                    ))}
                  </div>
                )}
                {msg.content}
              </div>
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
