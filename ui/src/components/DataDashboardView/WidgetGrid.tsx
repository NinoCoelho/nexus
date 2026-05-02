/**
 * WidgetGrid — LLM-populated artifacts pinned below the database tables.
 *
 * Each widget renders the markdown body produced by the most recent agent
 * refresh (chart fence, terse report, KPI, list). The grid handles:
 *
 *   - Refresh button per widget (kicks /widgets/<id>/refresh, listens for
 *     op_done, reloads the body).
 *   - Refresh-all button that runs widgets sequentially — back-end hosts a
 *     single LLM and 7+ parallel sessions made it look hung.
 *   - Daily auto-refresh: on mount, if a widget's `last_refreshed_at` isn't
 *     today (UTC) and `refresh: daily`, kick a refresh (also sequential).
 *     The user controls cost by sticking to daily — we never auto-refresh
 *     hourly.
 *   - Stale chip showing "Updated N hours ago" when the body is older than
 *     today.
 *
 * Storage lives in `<folder>/_widgets/<id>.md` and the widget config sits
 * in `_data.md` next to operations.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchWidgetContent,
  refreshWidget,
  refineWidget,
  type DashboardWidget,
  type WidgetSize,
} from "../../api/dashboard";
import { subscribeSessionEvents } from "../../api/chat";
import { useToast } from "../../toast/ToastProvider";
import MarkdownView from "../MarkdownView";

interface Props {
  folder: string;
  widgets: DashboardWidget[];
  onAdd: () => void;
  /** Optional wizard entry point. When omitted, only the simple "+ Widget"
   *  button shows. */
  onAddWizard?: () => void;
  onEdit: (widget: DashboardWidget) => void;
  onRemove: (widgetId: string) => void;
  /** Persist a size change for a widget. Caller upserts via `addWidget` so
   *  the saved result file stays linked. */
  onResize: (widget: DashboardWidget, size: WidgetSize) => void | Promise<void>;
  /** Forwarded so vault links inside report widgets behave like in chat. */
  onOpenInVault?: (path: string) => void;
}

const KIND_DEFAULT_SIZE: Record<string, WidgetSize> = {
  chart: "md",
  report: "md",
  kpi: "sm",
};

function effectiveSize(w: DashboardWidget): WidgetSize {
  return w.size ?? KIND_DEFAULT_SIZE[w.kind] ?? "md";
}

type WidgetStatus = "idle" | "running" | "failed";

interface WidgetState {
  body: string;
  status: WidgetStatus;
  error?: string;
  /** Set when the body refreshed successfully but failed to render
   *  client-side (e.g. malformed nexus-chart fence). Different from
   *  ``error``, which carries server-side refresh failures. */
  renderError?: string;
}

const KIND_ICON: Record<string, string> = {
  chart: "📊",
  report: "📝",
  kpi: "🔢",
};

function isoIsToday(iso: string | null): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return (
    d.getUTCFullYear() === now.getUTCFullYear() &&
    d.getUTCMonth() === now.getUTCMonth() &&
    d.getUTCDate() === now.getUTCDate()
  );
}

function freshness(iso: string | null): string {
  if (!iso) return "Never refreshed";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never refreshed";
  const ms = Date.now() - d.getTime();
  const hours = Math.floor(ms / (60 * 60 * 1000));
  if (hours < 1) return "Updated just now";
  if (hours < 24) return `Updated ${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `Updated ${days}d ago`;
}

export default function WidgetGrid({ folder, widgets, onAdd, onAddWizard, onEdit, onRemove, onResize, onOpenInVault }: Props) {
  const toast = useToast();
  const [states, setStates] = useState<Record<string, WidgetState>>({});
  // Single global refresh queue: every refresh (per-widget click, daily
  // auto-refresh, or "Refresh all") chains off this promise, so the backend
  // never sees more than one widget refresh in flight at a time. 7+ parallel
  // LLM sessions would peg the agent and look hung from the UI.
  const queueTail = useRef<Promise<void>>(Promise.resolve());
  const [queueLength, setQueueLength] = useState(0);
  // Track running session subs so unmount or re-refresh can close them.
  const subs = useRef<Record<string, { close: () => void }>>({});
  // Guard so the daily auto-refresh effect only fires once per mount/folder.
  const dailyFiredFor = useRef<string>("");
  // Snapshot of currently mounted widget ids so the auto-refresh effect's
  // dependency list stays stable; we read live config off the prop on fire.
  const widgetsRef = useRef(widgets);
  widgetsRef.current = widgets;

  const loadBody = useCallback(async (widgetId: string) => {
    try {
      const { content } = await fetchWidgetContent(folder, widgetId);
      setStates((s) => ({
        ...s,
        [widgetId]: {
          ...(s[widgetId] ?? { status: "idle" }),
          body: content,
          status: "idle",
          error: undefined,
          // Clear stale render errors so the next render gets a fresh shot.
          renderError: undefined,
        },
      }));
    } catch (e) {
      setStates((s) => ({
        ...s,
        [widgetId]: { body: s[widgetId]?.body ?? "", status: "failed", error: (e as Error).message },
      }));
    }
  }, [folder]);

  // Initial body fetch for every widget on mount / folder change.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const initial: Record<string, WidgetState> = {};
      for (const w of widgets) {
        try {
          const { content } = await fetchWidgetContent(folder, w.id);
          if (cancelled) return;
          initial[w.id] = { body: content, status: "idle" };
        } catch {
          // Network blip — leave the slot empty; the widget shows "Never refreshed".
          if (cancelled) return;
          initial[w.id] = { body: "", status: "idle" };
        }
      }
      if (!cancelled) setStates(initial);
    })();
    return () => {
      cancelled = true;
      Object.values(subs.current).forEach((s) => s.close());
      subs.current = {};
    };
    // We deliberately key only on folder + the set of widget ids so
    // re-ordering doesn't refetch everything; identity changes when widgets
    // are added/removed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, widgets.map((w) => w.id).join("|")]);

  // Resolves only after the op finishes (or fails) so callers can await
  // sequentially. The per-widget click-to-refresh button doesn't await this,
  // so its UX is unchanged.
  const startRefresh = useCallback((widget: DashboardWidget): Promise<void> => {
    setStates((s) => ({
      ...s,
      [widget.id]: { ...(s[widget.id] ?? { body: "" }), status: "running", error: undefined },
    }));
    return new Promise<void>((resolve) => {
      void (async () => {
        try {
          const { session_id } = await refreshWidget(folder, widget.id);
          // Close any prior subscription for this widget — the new run wins.
          const prev = subs.current[widget.id];
          if (prev) prev.close();
          const sub = subscribeSessionEvents(session_id, async (event) => {
            if (event.kind !== "op_done") return;
            const ok = event.data.status === "done";
            if (ok) {
              await loadBody(widget.id);
            } else {
              setStates((s) => ({
                ...s,
                [widget.id]: {
                  body: s[widget.id]?.body ?? "",
                  status: "failed",
                  error: event.data.error ?? undefined,
                },
              }));
            }
            const current = subs.current[widget.id];
            if (current) {
              current.close();
              delete subs.current[widget.id];
            }
            resolve();
          });
          subs.current[widget.id] = sub;
        } catch (e) {
          setStates((s) => ({
            ...s,
            [widget.id]: {
              body: s[widget.id]?.body ?? "",
              status: "failed",
              error: (e as Error).message,
            },
          }));
          toast.error(`Couldn't refresh "${widget.title}"`, { detail: (e as Error).message });
          resolve();
        }
      })();
    });
  }, [folder, loadBody, toast]);

  // Append a refresh to the global queue. Every entry point goes through
  // here, including the per-widget click — so even rapid clicks across many
  // widgets serialize into a single chain. Optimistically marks the widget
  // as running so the UI reflects the queued state immediately.
  const enqueueRefresh = useCallback((widget: DashboardWidget): void => {
    setStates((s) => ({
      ...s,
      [widget.id]: { ...(s[widget.id] ?? { body: "" }), status: "running", error: undefined },
    }));
    setQueueLength((n) => n + 1);
    queueTail.current = queueTail.current
      .catch(() => undefined)
      .then(() => startRefresh(widget))
      .finally(() => setQueueLength((n) => Math.max(0, n - 1)));
  }, [startRefresh]);

  // Daily auto-refresh: fire once per (folder + day) for any widget marked
  // `refresh: daily` whose last_refreshed_at isn't today. Manual widgets are
  // skipped — they only refresh on explicit click.
  useEffect(() => {
    const todayKey = new Date().toISOString().slice(0, 10);
    const fireKey = `${folder}|${todayKey}`;
    if (dailyFiredFor.current === fireKey) return;
    dailyFiredFor.current = fireKey;
    const stale = widgetsRef.current.filter(
      (w) => w.refresh === "daily" && !isoIsToday(w.last_refreshed_at),
    );
    for (const w of stale) enqueueRefresh(w);
    // We intentionally don't depend on `widgets` directly: if widgets shift
    // mid-session we don't want to re-fire today's auto-refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, enqueueRefresh]);

  const refreshAll = useCallback(() => {
    for (const w of widgets) enqueueRefresh(w);
  }, [widgets, enqueueRefresh]);

  // Capture render errors from embedded chart fences so the widget card can
  // show a friendly recovery UI instead of just the raw red error block.
  const handleRenderError = useCallback((widgetId: string, message: string) => {
    setStates((s) => {
      const prev = s[widgetId];
      if (prev?.renderError === message) return s;
      return {
        ...s,
        [widgetId]: { ...(prev ?? { body: "", status: "idle" }), renderError: message },
      };
    });
  }, []);

  // Refine: re-run the refresh with the prior failure context attached.
  // Always queued through the same global queue so it can't double up with
  // a manual click or daily auto-refresh in flight.
  const enqueueRefine = useCallback((widget: DashboardWidget, previous: string, errorMsg: string): void => {
    setStates((s) => ({
      ...s,
      [widget.id]: { ...(s[widget.id] ?? { body: "" }), status: "running", error: undefined, renderError: undefined },
    }));
    setQueueLength((n) => n + 1);
    queueTail.current = queueTail.current
      .catch(() => undefined)
      .then(() => new Promise<void>((resolve) => {
        void (async () => {
          try {
            const { session_id } = await refineWidget(folder, widget.id, previous, errorMsg);
            const prevSub = subs.current[widget.id];
            if (prevSub) prevSub.close();
            const sub = subscribeSessionEvents(session_id, async (event) => {
              if (event.kind !== "op_done") return;
              const ok = event.data.status === "done";
              if (ok) {
                await loadBody(widget.id);
              } else {
                setStates((s) => ({
                  ...s,
                  [widget.id]: {
                    body: s[widget.id]?.body ?? "",
                    status: "failed",
                    error: event.data.error ?? undefined,
                  },
                }));
              }
              const current = subs.current[widget.id];
              if (current) {
                current.close();
                delete subs.current[widget.id];
              }
              resolve();
            });
            subs.current[widget.id] = sub;
          } catch (e) {
            setStates((s) => ({
              ...s,
              [widget.id]: { body: s[widget.id]?.body ?? "", status: "failed", error: (e as Error).message },
            }));
            toast.error(`Couldn't refine "${widget.title}"`, { detail: (e as Error).message });
            resolve();
          }
        })();
      }))
      .finally(() => setQueueLength((n) => Math.max(0, n - 1)));
  }, [folder, loadBody, toast]);

  return (
    <div className="data-dash-widgets">
      <div className="data-dash-widgets-header">
        <div className="data-dash-widgets-actions">
          <button
            type="button"
            className="data-dash-action-btn"
            onClick={refreshAll}
            disabled={widgets.length === 0 || queueLength > 0}
            title={
              queueLength > 0
                ? `Refreshing — ${queueLength} widget${queueLength === 1 ? "" : "s"} left`
                : "Refresh every widget on this dashboard"
            }
          >
            {queueLength > 0 ? `↻ Refreshing… (${queueLength})` : "↻ Refresh all"}
          </button>
          <button
            type="button"
            className="data-dash-action-btn"
            onClick={onAdd}
            title="Add a new widget (simple form)"
          >
            + Widget
          </button>
          {onAddWizard && (
            <button
              type="button"
              className="data-dash-action-btn"
              onClick={onAddWizard}
              title="Design a widget with a wizard — describe what you want and the agent helps shape it."
            >
              ✨ Wizard
            </button>
          )}
        </div>
      </div>

      {widgets.length === 0 ? (
        <div className="data-dash-hint">
          No widgets yet — click <strong>+ Widget</strong> to pin a chart, report, KPI,
          or list driven by an LLM prompt.
        </div>
      ) : (
        <div className="data-dash-widget-grid">
          {widgets.map((w) => {
            const state = states[w.id] ?? { body: "", status: "idle" as WidgetStatus };
            const running = state.status === "running";
            const failed = state.status === "failed";
            const size = effectiveSize(w);
            return (
              <article
                key={w.id}
                className={`data-dash-widget data-dash-widget--${w.kind} data-dash-widget--size-${size}`}
              >
                <header className="data-dash-widget-head">
                  <span className="data-dash-widget-icon" aria-hidden>
                    {KIND_ICON[w.kind] ?? "📦"}
                  </span>
                  <h3 className="data-dash-widget-title" title={w.prompt}>
                    {w.title}
                  </h3>
                  <span className="data-dash-widget-stale" title={w.last_refreshed_at ?? "Never"}>
                    {freshness(w.last_refreshed_at)}
                  </span>
                  <div className="data-dash-widget-sizes" role="group" aria-label="Widget size">
                    {(["sm", "md", "lg"] as WidgetSize[]).map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`data-dash-widget-size-btn${size === s ? " data-dash-widget-size-btn--active" : ""}`}
                        onClick={() => void onResize(w, s)}
                        title={`Set size to ${s.toUpperCase()}`}
                        aria-pressed={size === s}
                      >
                        {s.toUpperCase()}
                      </button>
                    ))}
                  </div>
                  <div className="data-dash-widget-controls">
                    <button
                      type="button"
                      className="data-dash-widget-btn"
                      onClick={() => enqueueRefresh(w)}
                      disabled={running}
                      title={running ? "Refreshing…" : "Refresh"}
                    >
                      {running ? "…" : "↻"}
                    </button>
                    <button
                      type="button"
                      className="data-dash-widget-btn"
                      onClick={() => onEdit(w)}
                      title="Edit widget"
                      aria-label="Edit widget"
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      className="data-dash-widget-btn"
                      onClick={() => onRemove(w.id)}
                      title="Remove widget"
                    >
                      ×
                    </button>
                  </div>
                </header>
                <div className="data-dash-widget-body">
                  {(failed || state.renderError) ? (
                    <WidgetErrorCard
                      widget={w}
                      refreshError={failed ? state.error : undefined}
                      renderError={state.renderError}
                      previousBody={state.body}
                      onEdit={() => onEdit(w)}
                      onRefine={(prev, msg) => enqueueRefine(w, prev, msg)}
                    />
                  ) : state.body ? (
                    <MarkdownView
                      onVaultLinkPreview={onOpenInVault}
                      onChartError={(msg) => handleRenderError(w.id, msg)}
                    >{state.body}</MarkdownView>
                  ) : (
                    <div className="data-dash-hint">
                      {running ? "Running first refresh…" : "Not refreshed yet — click ↻."}
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface ErrorCardProps {
  widget: DashboardWidget;
  /** Set when the refresh session itself failed (server / agent error). */
  refreshError?: string;
  /** Set when the body refreshed but the client failed to render it
   *  (e.g. malformed nexus-chart fence). */
  renderError?: string;
  /** The agent's last output, shown collapsed so the user can see what went
   *  wrong and so the refine call can pass it to the next attempt. */
  previousBody: string;
  onEdit: () => void;
  onRefine: (previous: string, errorMsg: string) => void;
}

/**
 * Friendly recovery surface for a widget whose last refresh failed or whose
 * body couldn't render. Surfaces three affordances:
 *   - **Refine with agent** → re-runs the refresh seeded with the prior
 *     output + error so the agent can self-correct (e.g. emit YAML when its
 *     last try produced JSON).
 *   - **Edit prompt** → opens the existing AddWidgetModal in edit mode so
 *     the user can rephrase the prompt themselves.
 *   - The raw output collapsed in a ``<details>`` so power users can still
 *     inspect what the agent actually returned.
 */
function WidgetErrorCard({ widget, refreshError, renderError, previousBody, onEdit, onRefine }: ErrorCardProps) {
  const isRender = !refreshError && !!renderError;
  const message = isRender
    ? "I produced output that didn't render. Want me to try again?"
    : "Last refresh failed.";
  const detail = renderError ?? refreshError ?? "(no detail)";

  return (
    <div className="widget-error-card">
      <div className="widget-error-card-msg">{message}</div>
      <div className="widget-error-card-detail">{detail}</div>
      <div className="widget-error-card-actions">
        <button
          type="button"
          className="data-dash-action-btn data-dash-action-btn--primary"
          onClick={() => onRefine(previousBody, detail)}
          title="Re-run the refresh with the failure as context — the agent will try to self-correct."
        >
          ↻ Refine with agent
        </button>
        <button
          type="button"
          className="data-dash-action-btn"
          onClick={onEdit}
          title="Rephrase the widget prompt yourself."
        >
          ✎ Edit prompt
        </button>
      </div>
      {previousBody && (
        <details className="widget-error-card-raw">
          <summary>Show raw output ({widget.kind})</summary>
          <pre><code>{previousBody}</code></pre>
        </details>
      )}
    </div>
  );
}
