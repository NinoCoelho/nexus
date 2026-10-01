/**
 * BubblePanel — the coordinator popup chat.
 *
 * A render surface over the master session's ChatState (owned by App's
 * useChatSession) — closing it never stops a running turn. Draggable by
 * its header, resizable via the bottom-right grip (both persisted by the
 * parent). Pending HITL requests render inline; answering races safely
 * with the global ApprovalDialog (same resolve API, idempotent).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Send, Square, X, MapPin } from "lucide-react";
import type { UserRequestPayload } from "../../api";
import type { ChatState } from "../../types/chat";
import type { Message } from "../ChatView";
import AssistantMessage from "../AssistantMessage";
import FormRenderer from "../FormRenderer";
import { stripContextPreamble } from "../../contextPreamble";
import type { BubblePos } from "./Bubble";

export interface PanelSize {
  w: number;
  h: number;
}

export const MIN_W = 340;
export const MIN_H = 420;

interface Props {
  sessionId: string;
  name: string;
  state: ChatState;
  processing: boolean;
  hitl: UserRequestPayload | null;
  contextLabel: string | null;
  includeContext: boolean;
  onToggleContext: () => void;
  onSend: (text: string) => void;
  onStop: () => void;
  onRemoveQueued: (qid: string) => void;
  onAnswerHitl: (rid: string, answer: string | Record<string, unknown>) => void;
  onCancelHitl: (rid: string) => void;
  onHitlAnswered: (rid: string) => void;
  onMaximize: () => void;
  onClose: () => void;
  pos: BubblePos;
  onPosChange: (p: BubblePos) => void;
  size: PanelSize;
  onSizeChange: (s: PanelSize) => void;
  onOpenInVault?: (path: string) => void;
}

export function clampPanelRect(p: BubblePos, s: PanelSize): { pos: BubblePos; size: PanelSize } {
  const size: PanelSize = {
    w: Math.min(Math.max(s.w, MIN_W), window.innerWidth - 16),
    h: Math.min(Math.max(s.h, MIN_H), window.innerHeight - 16),
  };
  const pos: BubblePos = {
    x: Math.min(Math.max(p.x, 8), Math.max(8, window.innerWidth - size.w - 8)),
    y: Math.min(Math.max(p.y, 8), Math.max(8, window.innerHeight - size.h - 8)),
  };
  return { pos, size };
}

function HitlCard({ hitl, onAnswer, onCancel, onDone }: {
  hitl: UserRequestPayload;
  onAnswer: (rid: string, answer: string | Record<string, unknown>) => void;
  onCancel: (rid: string) => void;
  onDone: (rid: string) => void;
}) {
  const [text, setText] = useState("");
  const busy = useRef(false);
  const answer = (rid: string, a: string | Record<string, unknown>) => {
    if (busy.current) return;
    busy.current = true;
    onAnswer(rid, a);
    onDone(rid);
  };
  const cancel = () => {
    if (busy.current) return;
    busy.current = true;
    onCancel(hitl.request_id);
    onDone(hitl.request_id);
  };
  return (
    <div className="nxcb-hitl">
      <div className="nxcb-hitl-title">
        {hitl.form_title || "The coordinator needs you"}
      </div>
      <p className="nxcb-hitl-prompt">{hitl.prompt}</p>
      {hitl.kind === "form" && hitl.form_description && (
        <p className="nxcb-hitl-desc">{hitl.form_description}</p>
      )}
      {hitl.kind === "form" && hitl.fields && (
        <FormRenderer
          fields={hitl.fields}
          onSubmit={(values) => answer(hitl.request_id, values)}
          submitLabel="Submit"
        />
      )}
      {hitl.kind === "confirm" && (
        <div className="nxcb-hitl-row">
          <button type="button" className="nxcb-btn nxcb-btn--ghost" onClick={() => answer(hitl.request_id, "no")}>No</button>
          <button type="button" className="nxcb-btn nxcb-btn--primary" onClick={() => answer(hitl.request_id, "yes")}>Yes</button>
        </div>
      )}
      {hitl.kind === "choice" && (
        <div className="nxcb-hitl-row nxcb-hitl-row--wrap">
          {(hitl.choices ?? []).map((c) => (
            <button type="button" key={c} className="nxcb-btn nxcb-btn--ghost" onClick={() => answer(hitl.request_id, c)}>{c}</button>
          ))}
        </div>
      )}
      {hitl.kind === "text" && (
        <form
          className="nxcb-hitl-text"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            answer(hitl.request_id, text);
          }}
        >
          <input
            className="nxcb-hitl-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={hitl.default ?? "Type your answer…"}
            autoFocus
          />
          <button type="submit" className="nxcb-btn nxcb-btn--primary" disabled={!text.trim()}>Send</button>
        </form>
      )}
      <button type="button" className="nxcb-hitl-cancel" onClick={cancel}>Dismiss request</button>
    </div>
  );
}

export default function BubblePanel({
  sessionId,
  name,
  state,
  processing,
  hitl,
  contextLabel,
  includeContext,
  onToggleContext,
  onSend,
  onStop,
  onRemoveQueued,
  onAnswerHitl,
  onCancelHitl,
  onHitlAnswered,
  onMaximize,
  onClose,
  pos,
  onPosChange,
  size,
  onSizeChange,
  onOpenInVault,
}: Props) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const dragState = useRef<{ startX: number; startY: number; origin: BubblePos } | null>(null);
  const resizeState = useRef<{ startX: number; startY: number; origin: PanelSize } | null>(null);

  const visible = useMemo(
    () =>
      state.messages.filter(
        (m) =>
          (m.content ?? "").trim().length > 0 ||
          (m.timeline ?? []).length > 0 ||
          m.partial != null ||
          (m.attachments ?? []).length > 0,
      ),
    [state.messages],
  );

  const lastMsg: Message | undefined = state.messages[state.messages.length - 1];
  const streamingInProgress =
    state.thinking && lastMsg?.role === "assistant" && ((lastMsg.content ?? "").length > 0 || (lastMsg.timeline ?? []).length > 0);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [visible.length, state.thinking, hitl?.request_id, streamingInProgress]);

  // Auto-grow the composer textarea up to ~5 rows.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 110)}px`;
  }, [draft]);

  const onHeaderPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      // Don't start a drag from the action buttons.
      if ((e.target as HTMLElement).closest("button")) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      dragState.current = { startX: e.clientX, startY: e.clientY, origin: pos };
    },
    [pos],
  );
  const onHeaderPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const st = dragState.current;
      if (!st) return;
      const next = clampPanelRect(
        { x: st.origin.x + (e.clientX - st.startX), y: st.origin.y + (e.clientY - st.startY) },
        size,
      ).pos;
      onPosChange(next);
    },
    [size, onPosChange],
  );
  const onHeaderPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    dragState.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* noop */ }
  }, []);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      e.currentTarget.setPointerCapture(e.pointerId);
      resizeState.current = { startX: e.clientX, startY: e.clientY, origin: size };
    },
    [size],
  );
  const onResizePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const st = resizeState.current;
      if (!st) return;
      const next = clampPanelRect(pos, {
        w: st.origin.w + (e.clientX - st.startX),
        h: st.origin.h + (e.clientY - st.startY),
      }).size;
      onSizeChange(next);
    },
    [pos, onSizeChange],
  );
  const onResizePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    resizeState.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* noop */ }
  }, []);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    onSend(text);
  };

  return (
    <div
      className="nxcb-panel"
      style={{ left: pos.x, top: pos.y, width: size.w, height: size.h }}
      role="dialog"
      aria-label={`${name} master chat`}
    >
      <div
        className="nxcb-header"
        onPointerDown={onHeaderPointerDown}
        onPointerMove={onHeaderPointerMove}
        onPointerUp={onHeaderPointerUp}
      >
        <span className={`nxcb-status-dot${processing ? " nxcb-status-dot--on" : ""}`} aria-hidden="true" />
        <span className="nxcb-header-name">{name}</span>
        {contextLabel && (
          <span className="nxcb-context-chip" title={`Context attached to your messages: ${contextLabel}`}>
            <button
              type="button"
              className={`nxcb-context-toggle${includeContext ? " nxcb-context-toggle--on" : ""}`}
              onClick={onToggleContext}
              title={includeContext ? `Sending with context: ${contextLabel}. Click to exclude.` : "Context excluded. Click to attach."}
            >
              <MapPin size={11} />
            </button>
            <span className="nxcb-context-label">{contextLabel}</span>
          </span>
        )}
        <span className="nxcb-header-actions">
          <button type="button" className="nxcb-icon-btn" onClick={onMaximize} title="Open in main chat" aria-label="Maximize">
            <Maximize2 size={13} />
          </button>
          <button type="button" className="nxcb-icon-btn" onClick={onClose} title="Close (keeps processing)" aria-label="Close">
            <X size={14} />
          </button>
        </span>
      </div>

      <div ref={scrollRef} className="nxcb-messages">
        {visible.length === 0 && !state.thinking && (
          <div className="nxcb-empty">
            The deputy is listening. Ask anything — it sees every project and can dispatch work into any chat.
          </div>
        )}
        {visible.map((m, i) =>
          m.role === "assistant" ? (
            <div key={i} className="nxcb-asst">
              <AssistantMessage
                content={m.content}
                trace={m.trace}
                timeline={m.timeline}
                thinking={m.thinking}
                timestamp={m.timestamp}
                streaming={m.streaming}
                onOpenInVault={onOpenInVault}
                model={m.model}
                sessionId={sessionId}
                seq={m.seq}
                reconnecting={m.reconnecting}
              />
            </div>
          ) : (
            <div key={i} className="nxcb-user">
              {m.attachments && m.attachments.length > 0 && (
                <div className="nxcb-user-attachments">
                  {m.attachments.map((a, j) => (
                    <span key={j} className="nxcb-user-attachment">{a.name}</span>
                  ))}
                </div>
              )}
              {stripContextPreamble(m.content)}
            </div>
          ),
        )}
        {state.thinking && !streamingInProgress && (
          <div className="nxcb-thinking" aria-label="thinking">
            <span className="nxcb-thinking-dot" />
            <span className="nxcb-thinking-dot" />
            <span className="nxcb-thinking-dot" />
          </div>
        )}
      </div>

      {hitl && (
        <HitlCard key={hitl.request_id} hitl={hitl} onAnswer={onAnswerHitl} onCancel={onCancelHitl} onDone={onHitlAnswered} />
      )}

      {(state.queued ?? []).length > 0 && (
        <div className="nxcb-queued">
          {(state.queued ?? []).map((q) => (
            <span key={q.qid ?? q.text} className="nxcb-queued-chip">
              <span className="nxcb-queued-text">{q.text}</span>
              {q.qid && (
                <button type="button" className="nxcb-queued-x" onClick={() => onRemoveQueued(q.qid!)} aria-label="Remove queued message">
                  <X size={10} />
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      <div className="nxcb-composer">
        <textarea
          ref={taRef}
          className="nxcb-input"
          rows={1}
          value={draft}
          placeholder="Ask the coordinator…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {processing ? (
          <button type="button" className="nxcb-send nxcb-send--stop" onClick={onStop} title="Stop" aria-label="Stop">
            <Square size={12} />
          </button>
        ) : (
          <button
            type="button"
            className="nxcb-send"
            onClick={submit}
            disabled={!draft.trim()}
            title="Send"
            aria-label="Send"
          >
            <Send size={14} />
          </button>
        )}
      </div>

      <div
        className="nxcb-resize"
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panel"
      />
    </div>
  );
}
