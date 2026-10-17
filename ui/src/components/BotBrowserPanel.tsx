/**
 * BotBrowserPanel — watch-only live view of a bot's managed Chrome.
 *
 * Each bot's browser is a dedicated-profile Chrome window on the desktop
 * (isolated cookies/logins). This panel streams that window over CDP
 * screencast (SSE) so you can follow along from the UI; interaction
 * happens in the real desktop window, not here.
 */

import { useEffect, useRef, useState } from "react";
import {
  browserAction,
  browserStreamUrl,
  getBrowserStatus,
  stopBrowser,
  type BrowserStatus,
} from "../api";

interface Props {
  /** Context key: "project:<id>" (or "main"). */
  contextKey: string;
}

export default function BotBrowserPanel({ contextKey }: Props) {
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [info, setInfo] = useState<{ url?: string | null; title?: string | null }>({});
  const [streamError, setStreamError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const esRef = useRef<EventSource | null>(null);

  const running = status?.running === true;

  // Status poll (drives Start/Stop + stream lifecycle).
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      getBrowserStatus(contextKey)
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch(() => {});
    };
    poll();
    const timer = window.setInterval(poll, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [contextKey]);

  // Stream while running. Server-sent `error` events (e.g. Chrome still
  // booting) close the stream and retry after a short delay; transient
  // drops are handled by EventSource's native auto-reconnect.
  useEffect(() => {
    if (!running) return;
    const es = new EventSource(browserStreamUrl(contextKey));
    esRef.current = es;
    let retry: number | null = null;
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as {
          type: string;
          data?: string;
          url?: string | null;
          title?: string | null;
          error?: string;
        };
        if (ev.type === "frame" && ev.data) {
          setFrame(ev.data);
          setStreamError(null);
        } else if (ev.type === "info") {
          setInfo({ url: ev.url ?? null, title: ev.title ?? null });
        } else if (ev.type === "error") {
          setStreamError(ev.error || "stream error");
          es.close();
          retry = window.setTimeout(() => {
            setStreamError(null);
            setAttempt((a) => a + 1);
          }, 3000);
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    return () => {
      if (retry !== null) window.clearTimeout(retry);
      es.close();
      if (esRef.current === es) esRef.current = null;
    };
  }, [contextKey, running, attempt]);

  // Drop the stale frame when the browser stops.
  useEffect(() => {
    if (!running) setFrame(null);
  }, [running]);

  const refresh = () => {
    getBrowserStatus(contextKey)
      .then(setStatus)
      .catch(() => {});
  };

  const start = async () => {
    setBusy(true);
    try {
      const res = await browserAction(contextKey, "info");
      if (!res.ok) setStreamError(res.error || "failed to start browser");
    } catch {
      setStreamError("failed to start browser");
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await stopBrowser(contextKey);
    } catch {
      /* best-effort */
    } finally {
      setBusy(false);
      refresh();
    }
  };

  return (
    <div className="bot-browser">
      <div className="bots-chats-toolbar">
        <span className="bots-chats-toolbar-label">
          Browser ·{" "}
          {running ? (frame ? "live" : status?.connected ? "starting…" : "running") : "stopped"}
        </span>
        <span className="bot-browser-actions">
          {running ? (
            <button className="bots-cleanup-btn" disabled={busy} onClick={() => void stop()}>
              Stop browser
            </button>
          ) : (
            <button className="bots-new-chat-btn" disabled={busy} onClick={() => void start()}>
              Start browser
            </button>
          )}
        </span>
      </div>

      {(info.url || info.title) && (
        <div className="bot-browser-url" title={info.url ?? undefined}>
          {info.title || info.url}
        </div>
      )}

      <div className="bot-browser-view">
        {frame ? (
          <img src={`data:image/jpeg;base64,${frame}`} alt="Live view of this bot's browser" />
        ) : running ? (
          <div className="bots-workspace-empty">
            Connecting…
            <div className="bot-browser-sub">The browser window is opening on the desktop.</div>
          </div>
        ) : (
          <div className="bots-workspace-empty">
            This bot has no browser running.
            <div className="bot-browser-sub">
              Start it to open a dedicated Chrome window with its own isolated profile —
              cookies and logins stay separate from every other bot.
            </div>
          </div>
        )}
        {streamError && <div className="bot-browser-error">{streamError}</div>}
      </div>

      <div className="bot-browser-note">Watch-only — click and type in the Chrome window on your desktop.</div>
    </div>
  );
}
