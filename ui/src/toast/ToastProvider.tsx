/**
 * ToastProvider — lightweight toast notification system.
 *
 * Usage: const toast = useToast(); toast.success("Saved");
 * Supports info, success, warning, error levels with optional
 * detail text and action buttons. Toasts auto-dismiss after a
 * configurable duration (default 5s). Maximum 5 visible at once;
 * oldest is evicted when the limit is hit.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import { sounds } from "../hooks/useSounds";
import "./ToastProvider.css";

// ── Types ─────────────────────────────────────────────────────────────────────

export type ToastKind = "success" | "error" | "info" | "warning";

export interface ToastAction {
  label: string;
  onClick: () => void;
  /** When true, the toast stays open after the action click. Default false. */
  keepOpen?: boolean;
}

export interface ToastOptions {
  detail?: string;
  duration?: number;
  action?: ToastAction;
}

interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
  detail?: string;
  duration: number;
  action?: ToastAction;
  /** true while the exit animation is running */
  exiting: boolean;
}

interface ToastAPI {
  success: (message: string, opts?: ToastOptions) => string;
  error: (message: string, opts?: ToastOptions) => string;
  info: (message: string, opts?: ToastOptions) => string;
  warning: (message: string, opts?: ToastOptions) => string;
  update: (id: string, opts: Partial<ToastOptions> & { message?: string }) => void;
  dismiss: (id: string) => void;
}

const DEFAULT_DURATION = 4000;
const MAX_TOASTS = 5;

// ── Context ───────────────────────────────────────────────────────────────────

const ToastContext = createContext<ToastAPI>({
  success: () => "",
  error: () => "",
  info: () => "",
  warning: () => "",
  update: () => {},
  dismiss: () => {},
});

export function useToast(): ToastAPI {
  return useContext(ToastContext);
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function CheckIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="4 10 8.5 14.5 16 6" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="8" />
      <line x1="10" y1="6" x2="10" y2="11" />
      <circle cx="10" cy="14" r="0.5" fill="currentColor" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="8" />
      <line x1="10" y1="9" x2="10" y2="14" />
      <circle cx="10" cy="6" r="0.5" fill="currentColor" />
    </svg>
  );
}

function TriangleIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 3L18 17H2L10 3z" />
      <line x1="10" y1="9" x2="10" y2="13" />
      <circle cx="10" cy="15.5" r="0.5" fill="currentColor" />
    </svg>
  );
}

function KindIcon({ kind }: { kind: ToastKind }) {
  switch (kind) {
    case "success": return <CheckIcon />;
    case "error":   return <AlertIcon />;
    case "info":    return <InfoIcon />;
    case "warning": return <TriangleIcon />;
  }
}

// ── Single toast card ─────────────────────────────────────────────────────────

interface ToastCardProps {
  toast: Toast;
  onDismiss: (id: string) => void;
  onMouseEnter: (id: string) => void;
  onMouseLeave: (id: string) => void;
}

function ToastCard({ toast, onDismiss, onMouseEnter, onMouseLeave }: ToastCardProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={`toast-card toast-card--${toast.kind}${toast.exiting ? " toast-card--exit" : ""}`}
      onMouseEnter={() => onMouseEnter(toast.id)}
      onMouseLeave={() => onMouseLeave(toast.id)}
    >
      <span className="toast-icon">
        <KindIcon kind={toast.kind} />
      </span>
      <div className="toast-body">
        <span className="toast-message">{toast.message}</span>
        {toast.detail && <span className="toast-detail">{toast.detail}</span>}
      </div>
      {toast.action && (
        <button
          className="toast-action-btn"
          onClick={() => {
            toast.action!.onClick();
            if (!toast.action!.keepOpen) onDismiss(toast.id);
          }}
        >
          {toast.action.label}
        </button>
      )}
      <button
        className="toast-close"
        aria-label="Dismiss"
        onClick={() => onDismiss(toast.id)}
      >
        ×
      </button>
    </div>
  );
}

// ── Provider ──────────────────────────────────────────────────────────────────

let _nextId = 0;
function nextId() { return String(++_nextId); }

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Map<id, timerId> — timer IDs for auto-dismiss
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const startTimer = useCallback((id: string, duration: number) => {
    const tid = setTimeout(() => startExit(id), duration);
    timers.current.set(id, tid);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const clearTimer = useCallback((id: string) => {
    const tid = timers.current.get(id);
    if (tid != null) {
      clearTimeout(tid);
      timers.current.delete(id);
    }
  }, []);

  // Begin the exit animation, then remove after transition
  const startExit = useCallback((id: string) => {
    clearTimer(id);
    setToasts((prev) => prev.map((t) => t.id === id ? { ...t, exiting: true } : t));
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 300); // matches CSS transition duration
  }, [clearTimer]);

  const show = useCallback((kind: ToastKind, message: string, opts: ToastOptions = {}): string => {
    const id = nextId();
    const duration = opts.duration ?? DEFAULT_DURATION;
    const newToast: Toast = {
      id,
      kind,
      message,
      detail: opts.detail,
      duration,
      action: opts.action,
      exiting: false,
    };

    setToasts((prev) => {
      const next = [...prev, newToast];
      // Evict oldest if over limit
      if (next.length > MAX_TOASTS) {
        const evict = next[0];
        clearTimer(evict.id);
        return next.slice(1);
      }
      return next;
    });
    sounds.notification();

    if (duration > 0) startTimer(id, duration);
    return id;
  }, [clearTimer, startTimer]);

  const dismiss = useCallback((id: string) => {
    startExit(id);
  }, [startExit]);

  const update = useCallback((id: string, opts: Partial<ToastOptions> & { message?: string }) => {
    setToasts((prev) => prev.map((t) => t.id === id ? {
      ...t,
      message: opts.message ?? t.message,
      detail: opts.detail !== undefined ? opts.detail : t.detail,
      action: opts.action !== undefined ? opts.action : t.action,
    } : t));
  }, []);

  const onMouseEnter = useCallback((id: string) => {
    clearTimer(id);
  }, [clearTimer]);

  const onMouseLeave = useCallback((id: string) => {
    const toast = toasts.find((t) => t.id === id);
    if (toast && !toast.exiting && toast.duration > 0) {
      startTimer(id, toast.duration);
    }
  }, [toasts, startTimer]);

  // Stable API: each method is already useCallback-memoized, but the
  // wrapping object literal must also be memoized — otherwise consumers
  // that put `toast` in a useEffect/useCallback deps array re-run on
  // every toast add/remove, which can spiral into render loops.
  const success = useCallback((m: string, o?: ToastOptions) => show("success", m, o), [show]);
  const error   = useCallback((m: string, o?: ToastOptions) => show("error",   m, o), [show]);
  const info    = useCallback((m: string, o?: ToastOptions) => show("info",    m, o), [show]);
  const warning = useCallback((m: string, o?: ToastOptions) => show("warning", m, o), [show]);
  const api = useMemo<ToastAPI>(() => ({
    success, error, info, warning, update, dismiss,
  }), [success, error, info, warning, update, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-region" aria-label="Notifications">
        {toasts.map((t) => (
          <ToastCard
            key={t.id}
            toast={t}
            onDismiss={dismiss}
            onMouseEnter={onMouseEnter}
            onMouseLeave={onMouseLeave}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
