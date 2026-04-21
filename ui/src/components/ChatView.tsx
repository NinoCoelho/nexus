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
  hasModel: boolean | null;
  onOpenSettings: () => void;
  onOpenInVault?: (path: string) => void;
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
  hasModel,
  onOpenSettings,
  onOpenInVault,
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
  const visible = messages.filter((m) => (m.content ?? "").trim().length > 0);

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
            <AssistantMessage
              key={idx}
              content={msg.content}
              trace={msg.trace}
              timestamp={msg.timestamp}
              streaming={msg.streaming}
              onOpenInVault={onOpenInVault}
            />
          ) : (
            <div key={idx} className="user-msg">
              <div className="user-msg-meta">
                <span className="user-msg-label">You</span>
                <span className="user-msg-time">{fmt(msg.timestamp)}</span>
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
            />
          </div>
        </div>
      </div>
    </div>
  );
}
