import { useEffect, useRef } from "react";
import type { TraceEvent } from "../api";
import AssistantMessage from "./AssistantMessage";
import InputBar from "./InputBar";
import "./ChatView.css";

export interface Message {
  role: "user" | "assistant";
  content: string;
  trace?: TraceEvent[];
  timestamp: Date;
  streaming?: boolean;
  kind?: "limit";
  limitIterations?: number;
  attachments?: { name: string; vaultPath: string }[];
  model?: string;
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
  const streamingInProgress = thinking && lastMsg?.role === "assistant" && (lastMsg.content ?? "").length > 0;
  const visible = messages.filter(
    (m) => (m.content ?? "").trim().length > 0 || m.kind === "limit",
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
              <AssistantMessage
                key={idx}
                content={msg.content}
                trace={msg.trace}
                timestamp={msg.timestamp}
                streaming={msg.streaming}
                onOpenInVault={onOpenInVault}
                model={msg.model}
              />
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
