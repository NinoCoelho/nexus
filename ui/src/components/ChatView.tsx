import { useCallback, useEffect, useRef, useState } from "react";
import { getRouting, postChat, type TraceEvent } from "../api";
import AssistantMessage from "./AssistantMessage";
import InputBar from "./InputBar";
import "./ChatView.css";

export interface Message {
  role: "user" | "assistant";
  content: string;
  trace?: TraceEvent[];
  timestamp: Date;
}

interface Props {
  sessionId: string | null;
  onSessionCreated: (id: string, title: string) => void;
  onSkillsTouched: (names: string[]) => void;
  onOpenSettings: () => void;
  /** Bumps when the Settings drawer saves a change; triggers a routing refetch. */
  settingsRevision: number;
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function ChatView({
  sessionId,
  onSessionCreated,
  onSkillsTouched,
  onOpenSettings,
  settingsRevision,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [hasModel, setHasModel] = useState<boolean | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sessionSentRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    getRouting()
      .then((r) => {
        if (!cancelled) setHasModel((r.available_models?.length ?? 0) > 0);
      })
      .catch(() => {
        if (!cancelled) setHasModel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

  // NOTE: do NOT reset messages when sessionId changes. It transitions from
  // null to the assigned session id on the first successful send — resetting
  // here would wipe the conversation the user just started. A real "new chat"
  // is driven from App via a key change, which remounts this component.

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || thinking) return;

    const userMsg: Message = { role: "user", content: text, timestamp: new Date() };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setThinking(true);

    try {
      const res = await postChat(text, sessionId ?? undefined);
      sessionSentRef.current = true;

      if (!sessionId) {
        onSessionCreated(res.session_id, text.slice(0, 40));
      }
      if (res.skills_touched?.length) {
        onSkillsTouched(res.skills_touched);
      }

      const assistantMsg: Message = {
        role: "assistant",
        content: res.reply,
        trace: res.trace?.length ? res.trace : undefined,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      const errMsg: Message = {
        role: "assistant",
        content: `Error: ${err instanceof Error ? err.message : "request failed"}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setThinking(false);
    }
  }, [input, thinking, sessionId, onSessionCreated, onSkillsTouched]);

  return (
    <div className="chat-view">
      <div className="message-list">
        {messages.length === 0 && !thinking && hasModel === false && (
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
        {messages.length === 0 && !thinking && hasModel === true && (
          <div className="chat-empty">
            <p>Start a conversation with Nexus.</p>
          </div>
        )}
        {messages.map((msg, idx) =>
          msg.role === "assistant" ? (
            <AssistantMessage
              key={idx}
              content={msg.content}
              trace={msg.trace}
              timestamp={msg.timestamp}
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
        {thinking && (
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
              onChange={setInput}
              onSend={send}
              disabled={thinking}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
