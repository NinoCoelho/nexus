/**
 * DataChatBubble — floating chat panel for the active database.
 *
 * Renders only when the Data view has a database selected. Collapsed: a small
 * pill in the bottom-right. Expanded: a panel with messages + input. State
 * is owned by `useDatabaseChatSession`, which persists the session id in
 * `_data.md` so reloading the page re-attaches the conversation.
 *
 * Drill-down "Open in chat" buttons (e.g. on the related-rows panel inside
 * a table view) intentionally do NOT use this bubble — they land in the
 * main ChatView. The bubble is the database-scoped advisor; row-level
 * dispatches branch into the full chat surface.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import {
  useDatabaseChatSession,
  type BubbleMessage,
} from "../../hooks/useDatabaseChatSession";
import MarkdownView from "../MarkdownView";
import { useVaultLinkPreview } from "../vaultLink";
import "./DataChatBubble.css";

const SIZE_KEY = "nexus.dataBubbleSize";
const MIN_W = 320;
const MIN_H = 360;
const DEFAULT_W = 380;
const DEFAULT_H = 560;

function loadStoredSize(): { w: number; h: number } {
  try {
    const raw = localStorage.getItem(SIZE_KEY);
    if (!raw) return { w: DEFAULT_W, h: DEFAULT_H };
    const parsed = JSON.parse(raw) as { w?: number; h?: number };
    return {
      w: Math.max(MIN_W, Number(parsed.w) || DEFAULT_W),
      h: Math.max(MIN_H, Number(parsed.h) || DEFAULT_H),
    };
  } catch {
    return { w: DEFAULT_W, h: DEFAULT_H };
  }
}

export interface DataChatBubbleHandle {
  /** Imperative entrypoint used by OperationChips chat-kind operations. */
  runOperation: (prompt: string) => void;
  open: () => void;
}

interface Props {
  folder: string;
  databaseTitle?: string;
}

const DataChatBubble = forwardRef<DataChatBubbleHandle, Props>(function DataChatBubble(
  { folder, databaseTitle },
  ref,
) {
  const session = useDatabaseChatSession(folder);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const { onPreview: onVaultPreview, modal: vaultPreviewModal } = useVaultLinkPreview();

  // Persisted resize. The panel is anchored bottom-right of the viewport,
  // so the user grabs the top-left corner — increasing width/height grows
  // up and to the left, which feels right for a docked bubble.
  const [size, setSize] = useState<{ w: number; h: number }>(() => loadStoredSize());
  useEffect(() => {
    try { localStorage.setItem(SIZE_KEY, JSON.stringify(size)); } catch { /* noop */ }
  }, [size]);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startW = size.w;
    const startH = size.h;
    const onMove = (ev: MouseEvent) => {
      const dx = startX - ev.clientX;  // moving left => grow
      const dy = startY - ev.clientY;  // moving up => grow
      const nextW = Math.max(MIN_W, Math.min(window.innerWidth - 32, startW + dx));
      const nextH = Math.max(MIN_H, Math.min(window.innerHeight - 32, startH + dy));
      setSize({ w: nextW, h: nextH });
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "nwse-resize";
    document.body.style.userSelect = "none";
  }, [size]);

  const visibleMessages = useMemo<BubbleMessage[]>(
    () => session.messages.filter((m) => !m.hidden && m.content.trim().length > 0),
    [session.messages],
  );

  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [open, visibleMessages.length, session.thinking]);

  useImperativeHandle(ref, () => ({
    runOperation: (prompt: string) => {
      setOpen(true);
      void session.runOperation(prompt);
    },
    open: () => setOpen(true),
  }), [session]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim() || session.thinking) return;
    const text = draft;
    setDraft("");
    void session.send(text);
  };

  // Aggressive compaction is the bubble's only way to reclaim context — the
  // session is durable (one per database), so when it gets long we trim
  // medium-sized tool results in place rather than starting over.
  const handleCompact = () => {
    if (session.thinking || !session.sessionId) return;
    void session.send("/compact aggressive");
  };

  if (!open) {
    return (
      <button
        type="button"
        className="data-bubble-fab"
        onClick={() => setOpen(true)}
        title="Chat about this database"
        aria-label="Open data chat"
      >
        <span className="data-bubble-fab-icon">💬</span>
        <span className="data-bubble-fab-label">Chat</span>
      </button>
    );
  }

  return (
    <div
      className="data-bubble-panel"
      style={{ width: `${size.w}px`, height: `${size.h}px` }}
    >
      <div
        className="data-bubble-resize-handle"
        onMouseDown={handleResizeStart}
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize chat"
        title="Drag to resize"
      />
      <header className="data-bubble-header">
        <div className="data-bubble-title">
          <span className="data-bubble-title-icon">💬</span>
          <span className="data-bubble-title-text">{databaseTitle || folder.split("/").pop() || "Data"}</span>
        </div>
        <div className="data-bubble-header-actions">
          <button
            type="button"
            className="data-bubble-fresh-pill"
            onClick={handleCompact}
            disabled={session.thinking || !session.sessionId}
            title="Compact this chat aggressively (trims old tool results)"
          >
            Compact
          </button>
          <button
            type="button"
            className="data-bubble-close"
            onClick={() => setOpen(false)}
            title="Collapse"
            aria-label="Collapse chat"
          >
            ×
          </button>
        </div>
      </header>

      <div ref={scrollRef} className="data-bubble-messages">
        {visibleMessages.length === 0 && !session.bootstrapping && !session.thinking && (
          <div className="data-bubble-empty">
            Ask anything about this database. I have context on every table and saved operation.
          </div>
        )}
        {session.bootstrapping && (
          <div className="data-bubble-empty">Setting up context…</div>
        )}
        {visibleMessages.map((m, i) => (
          <div key={i} className={`data-bubble-msg data-bubble-msg--${m.role}`}>
            {m.role === "assistant" ? (
              m.content
                ? <MarkdownView onVaultLinkPreview={onVaultPreview} linkifyVaultPaths>{m.content}</MarkdownView>
                : (m.pending && <span className="data-bubble-spin" aria-label="thinking" />)
            ) : (
              m.content
            )}
          </div>
        ))}
        {session.thinking && !session.bootstrapping && visibleMessages.length > 0
          && !visibleMessages[visibleMessages.length - 1]?.pending && (
          <div className="data-bubble-msg data-bubble-msg--assistant data-bubble-msg--thinking">
            <span className="data-bubble-spin" aria-label="thinking" />
          </div>
        )}
        {session.error && (
          <div className="data-bubble-error">{session.error}</div>
        )}
      </div>

      <form className="data-bubble-input-row" onSubmit={handleSubmit}>
        <input
          type="text"
          className="data-bubble-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about this database…"
          disabled={session.thinking}
        />
        <button
          type="submit"
          className="data-bubble-send"
          disabled={!draft.trim() || session.thinking}
          aria-label="Send"
        >
          ↑
        </button>
      </form>
      {vaultPreviewModal}
    </div>
  );
});

export default DataChatBubble;
