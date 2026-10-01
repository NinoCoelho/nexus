/**
 * CoordinatorBubble — the master chat as a floating companion.
 *
 * Mounts once at App level while the coordinator is enabled. The orb
 * (draggable, animated) shows live status; the popup panel renders the
 * master session's ChatState — the same state the full ChatView uses, so
 * turns keep streaming no matter where the user navigates. On mount it
 * attaches to a running master turn (started from Telegram / a sweep /
 * before a reload). Messages sent here carry a `<context>` preamble
 * describing where the user is, so "latest on this project" / "create a
 * chart here" resolve naturally.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listProjects } from "../../api/projects";
import { cancelHitlRequest } from "../../api";
import { useCoordinatorBubbleStatus } from "../../hooks/useCoordinatorBubbleStatus";
import type { ChatState } from "../../types/chat";
import {
  buildContextPreamble,
  describeBubbleLocation,
  type BubbleContextInfo,
  type BubbleLocation,
} from "../../contextPreamble";
import Bubble, { ORB_SIZE, clampBubblePos, type BubblePos } from "./Bubble";
import BubblePanel, { MIN_H, MIN_W, clampPanelRect, type PanelSize } from "./BubblePanel";
import "./CoordinatorBubble.css";

const POS_KEY = "nexus.coordinatorBubble.pos";
const PANEL_POS_KEY = "nexus.coordinatorBubble.panelPos";
const SIZE_KEY = "nexus.coordinatorBubble.size";
const CONTEXT_KEY = "nexus.coordinatorBubble.context";

const DEFAULT_W = 420;
const DEFAULT_H = 580;

function loadJSON<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function storeJSON(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode etc. */
  }
}

function defaultOrbPos(): BubblePos {
  return clampBubblePos({ x: window.innerWidth - ORB_SIZE - 24, y: window.innerHeight - ORB_SIZE - 24 });
}

interface Props {
  sessionId: string | null;
  name: string;
  state: ChatState | undefined;
  location: BubbleLocation;
  sendToSession: (sessionId: string, text: string) => Promise<void>;
  stopSession: (sessionId: string) => void;
  attachToSession: (sessionId: string) => Promise<void>;
  respondForSession: (sessionId: string, requestId: string, answer: string | Record<string, unknown>) => Promise<void>;
  removeQueuedForSession: (sessionId: string, qid: string) => void;
  /** Bumped after every completed turn — used to refresh project names. */
  sessionsRevision: number;
  onMaximize: () => void;
  /** Drop the request from the global ApprovalDialog queue (answered inline here). */
  onHitlHandled: (requestId: string) => void;
  onOpenInVault?: (path: string) => void;
}

export default function CoordinatorBubble({
  sessionId,
  name,
  state,
  location,
  sendToSession,
  stopSession,
  attachToSession,
  respondForSession,
  removeQueuedForSession,
  sessionsRevision,
  onMaximize,
  onHitlHandled,
  onOpenInVault,
}: Props) {
  const status = useCoordinatorBubbleStatus(sessionId);
  const [open, setOpen] = useState(false);
  const [orbPos, setOrbPos] = useState<BubblePos>(() => loadJSON<BubblePos>(POS_KEY) ?? defaultOrbPos());
  const [panelPos, setPanelPos] = useState<BubblePos | null>(loadJSON<BubblePos>(PANEL_POS_KEY));
  const [size, setSize] = useState<PanelSize>(() => {
    const s = loadJSON<PanelSize>(SIZE_KEY);
    return s ? { w: Math.max(s.w, MIN_W), h: Math.max(s.h, MIN_H) } : { w: DEFAULT_W, h: DEFAULT_H };
  });
  const [includeContext, setIncludeContext] = useState(() => {
    try {
      return localStorage.getItem(CONTEXT_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const [showCheck, setShowCheck] = useState(false);
  const [projectNames, setProjectNames] = useState<Map<string, string>>(new Map());

  // Project names for the context label (id → name). Refreshed whenever
  // sessions/projects change (cheap list call).
  useEffect(() => {
    let cancelled = false;
    listProjects()
      .then((projects) => {
        if (cancelled) return;
        setProjectNames(new Map(projects.map((p) => [p.id, p.name])));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionsRevision]);

  // Attach on mount / session identity change / popup open: load history
  // and re-stream a running turn so the bubble is live even with the popup
  // closed (turn started from Telegram / a sweep / before a reload).
  // attachToSession changes identity on every chat-state mutation — keep it
  // in a ref so this effect doesn't refire per keystroke.
  const attachRef = useRef(attachToSession);
  useEffect(() => {
    attachRef.current = attachToSession;
  }, [attachToSession]);
  const hasState = !!state;
  useEffect(() => {
    if (!sessionId) return;
    void attachRef.current(sessionId);
  }, [sessionId, open, hasState]);

  // "Completed" badge: shows for a few seconds after a settle.
  useEffect(() => {
    if (!status.settledAt) return;
    setShowCheck(true);
    const t = setTimeout(() => setShowCheck(false), 4200);
    return () => clearTimeout(t);
  }, [status.settledAt]);

  // Re-clamp positions when the viewport shrinks.
  useEffect(() => {
    const onResize = () => {
      setOrbPos((p) => {
        const next = clampBubblePos(p);
        return next.x === p.x && next.y === p.y ? p : next;
      });
      setPanelPos((p) => {
        if (!p) return p;
        const { pos } = clampPanelRect(p, { w: MIN_W, h: MIN_H });
        return pos.x === p.x && pos.y === p.y ? p : pos;
      });
      setSize((s) => {
        const { size: next } = clampPanelRect({ x: 0, y: 0 }, s);
        return next.w === s.w && next.h === s.h ? s : next;
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const handleOrbPosChange = useCallback((p: BubblePos) => {
    setOrbPos(p);
  }, []);
  // Persist positions debounced — pointer-move fires per frame.
  useEffect(() => {
    const t = setTimeout(() => storeJSON(POS_KEY, orbPos), 300);
    return () => clearTimeout(t);
  }, [orbPos]);

  const toggleOpen = useCallback(() => setOpen((v) => !v), []);

  const contextInfo: BubbleContextInfo | null = useMemo(
    () => describeBubbleLocation(location, (id) => projectNames.get(id)),
    [location, projectNames],
  );

  const effectivePanelPos: BubblePos = useMemo(() => {
    if (panelPos) return panelPos;
    // First open, no stored position: dock above the orb, right-aligned.
    const x = orbPos.x + ORB_SIZE / 2 - DEFAULT_W / 2;
    const y = orbPos.y - DEFAULT_H - 12;
    if (y >= 8) return clampPanelRect({ x, y }, { w: DEFAULT_W, h: DEFAULT_H }).pos;
    // No room above — place below / beside.
    return clampPanelRect({ x: orbPos.x + ORB_SIZE + 12, y: orbPos.y }, { w: DEFAULT_W, h: DEFAULT_H }).pos;
  }, [panelPos, orbPos]);

  const handlePanelPosChange = useCallback((p: BubblePos) => {
    setPanelPos(p);
    storeJSON(PANEL_POS_KEY, p);
  }, []);
  const handleSizeChange = useCallback((s: PanelSize) => {
    setSize(s);
  }, []);
  const sizePersistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (sizePersistTimer.current) clearTimeout(sizePersistTimer.current);
    sizePersistTimer.current = setTimeout(() => storeJSON(SIZE_KEY, size), 400);
    return () => {
      if (sizePersistTimer.current) clearTimeout(sizePersistTimer.current);
    };
  }, [size]);

  const toggleContext = useCallback(() => {
    setIncludeContext((v) => {
      const next = !v;
      try {
        localStorage.setItem(CONTEXT_KEY, next ? "1" : "0");
      } catch { /* noop */ }
      return next;
    });
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      if (!sessionId) return;
      const payload = includeContext && contextInfo ? buildContextPreamble(contextInfo) + text : text;
      void sendToSession(sessionId, payload);
    },
    [sessionId, includeContext, contextInfo, sendToSession],
  );

  const handleAnswerHitl = useCallback(
    (rid: string, answer: string | Record<string, unknown>) => {
      if (!sessionId) return;
      void respondForSession(sessionId, rid, answer);
    },
    [sessionId, respondForSession],
  );

  const handleCancelHitl = useCallback(
    (rid: string) => {
      if (!sessionId) return;
      void cancelHitlRequest(sessionId, rid).catch(() => {});
    },
    [sessionId],
  );

  const handleHitlHandled = useCallback(
    (rid: string) => {
      status.clearHitl();
      onHitlHandled(rid);
    },
    [status, onHitlHandled],
  );

  if (!sessionId) return null;

  const processing = !!state?.thinking || status.detached;
  const lastVisible = state?.messages[state?.messages.length - 1];
  const errored = !!lastVisible?.partial && !state?.thinking;

  const mood: "idle" | "processing" | "hitl" | "error" | "settled" = status.hitl
    ? "hitl"
    : errored
      ? "error"
      : processing
        ? "processing"
        : showCheck
          ? "settled"
          : "idle";

  return (
    <>
      <Bubble
        status={mood}
        name={name}
        open={open}
        onToggle={toggleOpen}
        pos={orbPos}
        onPosChange={handleOrbPosChange}
      />
      {open && state && (
        <BubblePanel
          sessionId={sessionId}
          name={name}
          state={state}
          processing={processing}
          hitl={status.hitl}
          contextLabel={contextInfo?.label ?? null}
          includeContext={includeContext}
          onToggleContext={toggleContext}
          onSend={handleSend}
          onStop={() => stopSession(sessionId)}
          onRemoveQueued={(qid) => removeQueuedForSession(sessionId, qid)}
          onAnswerHitl={handleAnswerHitl}
          onCancelHitl={handleCancelHitl}
          onHitlAnswered={handleHitlHandled}
          onMaximize={() => {
            setOpen(false);
            onMaximize();
          }}
          onClose={() => setOpen(false)}
          pos={effectivePanelPos}
          onPosChange={handlePanelPosChange}
          size={size}
          onSizeChange={handleSizeChange}
          onOpenInVault={onOpenInVault}
        />
      )}
    </>
  );
}
