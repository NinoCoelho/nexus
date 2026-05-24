import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TimelineStep } from "./ChatView";
import { killTerminalProc, subscribeSessionEvents } from "../api/chat";
import type { SessionEvent } from "../api/chat";
import "./TerminalLiveModal.css";

interface Props {
  step: TimelineStep;
  sessionId: string;
  onClose: () => void;
}

const LINE_OPTIONS = [50, 100, 200, 500] as const;

export default function TerminalLiveModal({ step, sessionId, onClose }: Props) {
  const [output, setOutput] = useState(step.live_output ?? "");
  const [autoScroll, setAutoScroll] = useState(true);
  const [lineLimit, setLineLimit] = useState<number>(100);
  const [killing, setKilling] = useState(false);
  const [killed, setKilled] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const containerRef = useRef<HTMLPreElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const startTimeRef = useRef(Date.now());

  const callId = step.call_id ?? "";
  const isRunning = step.status === "pending" && !killed;
  const cmd = useMemo(() => {
    const a = step.args as Record<string, unknown> | undefined;
    return typeof a?.command === "string" ? a.command : "";
  }, [step.args]);

  // Subscribe to terminal_output events from the session SSE channel
  useEffect(() => {
    const sub = subscribeSessionEvents(sessionId, (event: SessionEvent) => {
      if (event.kind !== "terminal_output") return;
      if (event.data.call_id && event.data.call_id !== callId) return;
      const chunk = (event.data.stdout ?? "") + (event.data.stderr ?? "");
      if (!chunk) return;
      setOutput((prev) => prev + chunk);
    });
    return () => sub.close();
  }, [sessionId, callId]);

  // Elapsed timer
  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [isRunning]);

  // Auto-scroll
  useEffect(() => {
    if (!autoScroll || !containerRef.current) return;
    containerRef.current.scrollTop = containerRef.current.scrollHeight;
  }, [output, autoScroll]);

  // Detect user scroll-up to disable auto-scroll
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    if (!atBottom && autoScroll) setAutoScroll(false);
  }, [autoScroll]);

  // Keyboard
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Kill handler
  const handleKill = useCallback(async () => {
    if (!callId || killing) return;
    setKilling(true);
    try {
      await killTerminalProc(sessionId, callId);
      setKilled(true);
    } catch {
      setKilling(false);
    }
  }, [sessionId, callId, killing]);

  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === backdropRef.current) onClose();
  }

  const displayLines = useMemo(() => {
    const lines = output.split("\n");
    if (lines.length <= lineLimit) return output;
    return lines.slice(-lineLimit).join("\n");
  }, [output, lineLimit]);

  const fmtElapsed = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}:${sec.toString().padStart(2, "0")}` : `${sec}s`;
  };

  return (
    <div className="tlm-backdrop" ref={backdropRef} onClick={handleBackdropClick}>
      <div className="tlm-modal">
        <div className="tlm-header">
          <span className="tlm-icon">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="4 4 8 8 4 12" />
              <line x1="8" y1="12" x2="13" y2="12" />
            </svg>
          </span>
          <span className="tlm-title">Terminal</span>
          {isRunning && (
            <span className="tlm-elapsed">{fmtElapsed(elapsed)}</span>
          )}
          {killed && <span className="tlm-badge tlm-badge--killed">killed</span>}
          {!isRunning && !killed && (
            <span className="tlm-badge tlm-badge--done">done</span>
          )}
          <div className="tlm-spacer" />
          <button className="tlm-close" onClick={onClose} type="button" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="3" y1="3" x2="13" y2="13" />
              <line x1="13" y1="3" x2="3" y2="13" />
            </svg>
          </button>
        </div>
        {cmd && (
          <div className="tlm-cmd">
            <code>{cmd.length > 200 ? cmd.slice(0, 200) + "…" : cmd}</code>
          </div>
        )}
        <div className="tlm-toolbar">
          <label className="tlm-lines-label">
            Lines
            <select
              className="tlm-lines-select"
              value={lineLimit}
              onChange={(e) => setLineLimit(Number(e.target.value))}
            >
              {LINE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <button
            className={`tlm-scroll-btn${autoScroll ? " tlm-scroll-btn--active" : ""}`}
            onClick={() => {
              setAutoScroll(!autoScroll);
              if (!autoScroll && containerRef.current) {
                containerRef.current.scrollTop = containerRef.current.scrollHeight;
              }
            }}
            type="button"
          >
            {autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}
          </button>
          <div className="tlm-spacer" />
          {isRunning && (
            <button
              className="tlm-kill-btn"
              onClick={handleKill}
              disabled={killing}
              type="button"
            >
              {killing ? "Killing…" : "Kill process"}
            </button>
          )}
        </div>
        <div className="tlm-body">
          <pre
            className="tlm-output"
            ref={containerRef}
            onScroll={handleScroll}
          >
            {displayLines || (isRunning ? "Waiting for output…" : "(no output)")}
          </pre>
        </div>
      </div>
    </div>
  );
}
