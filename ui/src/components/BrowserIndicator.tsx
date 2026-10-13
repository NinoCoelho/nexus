/**
 * BrowserIndicator — header indicator for the managed-Chrome computer-use
 * subsystem (replaces the old full-page Browser view).
 *
 * Icon states: dim (nothing running) → green steady (Chrome running) →
 * green BLINKING while there is activity (each browser_event re-arms a
 * 10s window; the agent driving the browser keeps it lit).
 *
 * Hover → thumbnail card (live screenshot + url/title, loaded on hover).
 * Click → popup panel (bell pattern: relative anchor, click-outside +
 * Escape): live screenshot with click-to-takeover (scaled to Chrome's
 * fixed 1440×900 viewport), context switcher (main + projects), activity
 * log, stop/start. Everything rides the shared notifications SSE
 * (browser_event) — no dedicated channel.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Power, RefreshCw } from "lucide-react";
import "./BrowserIndicator.css";
import {
  browserAction,
  browserScreenshotUrl,
  listBrowserInstances,
  stopBrowser,
  type BrowserStatus,
} from "../api/browser";
import { subscribeGlobalNotifications } from "../api/chat";
import type { BrowserEventPayload } from "../api/chat";
import { IconBrowser } from "./Sidebar/icons";

/** Fixed logical viewport pinned at Chrome launch (--window-size). */
const VIEWPORT_W = 1440;
const VIEWPORT_H = 900;
/** How long the icon keeps blinking after the last browser_event. */
const ACTIVE_WINDOW_MS = 10_000;
const POLL_MS = 10_000;
const LOG_MAX = 8;
// Actions that can change what the page looks like — refresh the live shot
// when the agent performs one. "screenshot" is deliberately ABSENT: the
// capture endpoint itself emits a browser_event(screenshot), so including
// it would make every image load re-trigger itself (infinite refresh loop).
const VISUAL_ACTIONS = new Set(["navigate", "click", "type", "scroll", "js"]);

interface Props {
  /** Browser context of the currently open chat ("main" | "project:<id>") —
   * computed by App from the active session's project. The indicator
   * follows it: switching chats re-targets the thumbnail/popup, and the
   * steady green reflects THIS context's Chrome. Manual selection in the
   * popup lasts until the next chat switch. */
  contextKey?: string;
}

export default function BrowserIndicator({ contextKey = "main" }: Props) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
  const [instances, setInstances] = useState<BrowserStatus[]>([]);
  const [activeKey, setActiveKey] = useState(contextKey);
  const [active, setActive] = useState(false);
  const [events, setEvents] = useState<BrowserEventPayload[]>([]);
  const [shotTs, setShotTs] = useState(0);
  const [shotOk, setShotOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const activeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keyRef = useRef(activeKey);
  keyRef.current = activeKey;
  // Portal anchor (fixed coords) for the thumb + popup. They render via
  // createPortal to document.body — the header's .app-main wrapper lets the
  // chat content win hit-testing over header-anchored absolute panels (the
  // same reason GlobalSpinner portals its dropdown).
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);

  // Follow the open chat's browser context. A chat switch re-targets the
  // indicator (overriding any manual pick in the popup) and resets the
  // per-key shot state — same reset the manual tab switch performs.
  useEffect(() => {
    setActiveKey(contextKey);
    setShotOk(false);
    setShotTs(0);
  }, [contextKey]);

  const refresh = useCallback(async () => {
    try {
      setInstances(await listBrowserInstances());
    } catch {
      setInstances([]);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useLayoutEffect(() => {
    if (!(open || hover) || !wrapRef.current) {
      setPos(null);
      return;
    }
    const rect = wrapRef.current.getBoundingClientRect();
    setPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
    return () => setPos(null);
  }, [open, hover]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (wrapRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Activity feed: blink on every browser_event (any context — the icon
  // answers "is the agent browsing right now?"); log the recent ones; keep
  // the popup screenshot fresh for visual actions on the active context.
  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind !== "browser_event") return;
      const data = event.data as BrowserEventPayload;
      setActive(true);
      if (activeTimer.current) clearTimeout(activeTimer.current);
      activeTimer.current = setTimeout(() => setActive(false), ACTIVE_WINDOW_MS);
      setEvents((prev) => [data, ...prev].slice(0, LOG_MAX));
      if (data.action === "stopped") {
        setShotOk(false);
        setShotTs(0);
        refresh();
        return;
      }
      if (data.key === keyRef.current && VISUAL_ACTIONS.has(data.action)) {
        setShotTs(Date.now());
      }
    });
    return () => {
      sub.close();
      if (activeTimer.current) clearTimeout(activeTimer.current);
    };
  }, [refresh]);

  const status = instances.find((i) => i.key === activeKey) ?? null;
  const running = !!status && (status.connected || status.running);
  const otherRunning = instances.some((i) => i.key !== activeKey && (i.connected || i.running));
  const lastEvent = events.find((e) => e.key === activeKey) ?? null;
  const headline = lastEvent?.title || lastEvent?.url || status?.url || status?.title || null;

  // Load a first screenshot when it becomes useful (popup opened or hover
  // started) — the capture endpoint launches Chrome when needed, so we
  // never fire it speculatively.
  useEffect(() => {
    if (running && (open || hover) && shotTs === 0) setShotTs(Date.now());
  }, [running, open, hover, shotTs]);

  const act = useCallback(
    async (action: string, args: Record<string, unknown> = {}) => {
      setBusy(true);
      try {
        await browserAction(keyRef.current, action, args);
      } catch {
        /* surfaced via the activity log / status */
      } finally {
        setBusy(false);
        refresh();
      }
    },
    [refresh],
  );

  const takeClick = useCallback(
    async (e: React.MouseEvent<HTMLImageElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * VIEWPORT_W;
      const y = ((e.clientY - rect.top) / rect.height) * VIEWPORT_H;
      await act("click", { x: Math.round(x), y: Math.round(y) });
      setTimeout(() => setShotTs(Date.now()), 700);
    },
    [act],
  );

  const stop = useCallback(async () => {
    setBusy(true);
    try {
      await stopBrowser(keyRef.current);
    } catch {
      /* best-effort */
    } finally {
      setBusy(false);
      refresh();
    }
  }, [refresh]);

  // Icon states: dim (nothing anywhere) → amber (a browser runs in ANOTHER
  // context — visible so cross-context activity is never hidden) → green
  // (the open chat's own browser). The blink is global: it answers "is the
  // agent browsing right now?", wherever that browsing happens.
  const iconClass = active
    ? "header-btn nxb-btn nxb-btn--active"
    : running
      ? "header-btn nxb-btn nxb-btn--running"
      : otherRunning
        ? "header-btn nxb-btn nxb-btn--other"
        : "header-btn nxb-btn";
  const iconTitle = active
    ? `Browser activity (${activeKey})`
    : running
      ? `Managed Chrome running (${activeKey})`
      : otherRunning
        ? `Another context's Chrome is running (viewing ${activeKey})`
        : `Managed Chrome idle (${activeKey})`;

  return (
    <div
      className="nxb-wrap"
      ref={wrapRef}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <button
        type="button"
        aria-label="Browser"
        title={iconTitle}
        className={iconClass}
        onClick={() => setOpen((v) => !v)}
      >
        <IconBrowser />
      </button>

      {hover && !open && pos && createPortal(
        <div
          className="nxb-portal nxb-thumb"
          style={{ top: pos.top, right: pos.right }}
          role="tooltip"
        >
          {shotTs > 0 ? (
            <img
              key={shotTs}
              src={browserScreenshotUrl(activeKey, shotTs)}
              alt=""
              onLoad={() => setShotOk(true)}
              onError={() => setShotOk(false)}
              style={{ display: shotOk ? "block" : "none" }}
            />
          ) : null}
          {!shotOk && (
            <div className="nxb-thumb-empty">{running ? "capturing…" : "browser idle"}</div>
          )}
          {headline && <div className="nxb-thumb-title">{headline}</div>}
        </div>,
        document.body,
      )}

      {open && pos && createPortal(
        <div
          className="nxb-portal nxb-panel"
          ref={panelRef}
          style={{ top: pos.top, right: pos.right }}
          role="dialog"
        >
          {instances.length > 1 && (
            <div className="nxb-tabs">
              {instances.map((inst) => (
                <button
                  key={inst.key}
                  className={`nxb-tab${inst.key === activeKey ? " is-active" : ""}`}
                  onClick={() => {
                    setActiveKey(inst.key);
                    setShotOk(false);
                    setShotTs(0);
                  }}
                >
                  <span
                    className={`nxb-dot nxb-dot--${inst.connected ? "on" : inst.running ? "idle" : "off"}`}
                  />
                  {inst.key === "main" ? "Main" : inst.key.replace(/^project:/, "")}
                </button>
              ))}
            </div>
          )}

          <div className="nxb-shot">
            {shotTs > 0 ? (
              <img
                key={shotTs}
                src={browserScreenshotUrl(activeKey, shotTs)}
                alt="Managed Chrome live view"
                onLoad={() => setShotOk(true)}
                onError={() => setShotOk(false)}
                onClick={takeClick}
                draggable={false}
                style={{ display: shotOk ? "block" : "none" }}
              />
            ) : null}
            {!shotOk && (
              <div className="nxb-shot-empty" style={{ minHeight: 160 }}>
                {running ? (
                  shotTs > 0 ? (
                    <span
                      className="gs-spinner"
                      style={{ width: 18, height: 18, borderWidth: 3 }}
                    />
                  ) : (
                    <p>Chrome is running</p>
                  )
                ) : (
                  <>
                    <p>This browser isn't running</p>
                    <button
                      className="nxb-mini-btn"
                      onClick={async () => {
                        await act("info");
                        setShotTs(Date.now());
                      }}
                      disabled={busy}
                    >
                      Start Chrome
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          <div className="nxb-bar">
            <span className="nxb-url" title={headline ?? undefined}>
              {headline || "—"}
            </span>
            <button
              className="nxb-mini-btn"
              onClick={() => setShotTs(Date.now())}
              disabled={!running || busy}
              title="Refresh view"
            >
              <RefreshCw size={12} />
            </button>
            <button
              className="nxb-mini-btn nxb-mini-btn--danger"
              onClick={stop}
              disabled={!running || busy}
              title="Stop this Chrome (profile is kept)"
            >
              <Power size={12} />
            </button>
          </div>

          <div className="nxb-log">
            {events.length === 0 ? (
              <p className="nxb-log-empty">No browser activity yet.</p>
            ) : (
              events.map((ev, i) => (
                <div key={i} className={`nxb-log-row${ev.error ? " is-error" : ""}`}>
                  <span className="nxb-log-key">
                    {ev.key === "main" ? "main" : ev.key.replace(/^project:/, "")}
                  </span>
                  <span className="nxb-log-action">{ev.action}</span>
                  <span className="nxb-log-detail">{ev.error || ev.title || ev.url || ""}</span>
                </div>
              ))
            )}
          </div>
          <p className="nxb-hint">Click the screenshot to take over — coordinates map to the page.</p>
        </div>,
        document.body,
      )}
    </div>
  );
}
