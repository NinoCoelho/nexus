import { useCallback, useEffect, useRef, useState } from "react";
import { postChat, type TraceEvent } from "../api";
import AssistantMessage from "./AssistantMessage";
import SkillChipRow from "./SkillChipRow";
import InputBar from "./InputBar";
import ContextBar from "./ContextBar";
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
  pulsingSkills: Set<string>;
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function ChatView({
  sessionId,
  onSessionCreated,
  onSkillsTouched,
  pulsingSkills,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [context, setContext] = useState("");
  const [contextDismissed, setContextDismissed] = useState(false);
  const [thinking, setThinking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sessionSentRef = useRef(false);

  useEffect(() => {
    setMessages([]);
    setContext("");
    setContextDismissed(false);
    setInput("");
    sessionSentRef.current = false;
  }, [sessionId]);

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
      const ctx = sessionSentRef.current ? undefined : context || undefined;
      const res = await postChat(text, sessionId ?? undefined, ctx);
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
  }, [input, thinking, context, sessionId, onSessionCreated, onSkillsTouched]);

  const handleChipClick = useCallback((skillName: string) => {
    setInput(`Use the ${skillName} skill: `);
  }, []);

  const showContextBar =
    messages.length === 0 && !sessionSentRef.current && !contextDismissed;

  return (
    <div className="chat-view">
      <div className="message-list">
        {messages.length === 0 && !thinking && (
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
          <SkillChipRow pulsing={pulsingSkills} onChipClick={handleChipClick} />
          <div className="input-stack">
            {showContextBar && (
              <ContextBar
                value={context}
                onChange={setContext}
                onDismiss={() => setContextDismissed(true)}
                disabled={thinking}
              />
            )}
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
