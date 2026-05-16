import { useCallback, useEffect, useRef, useState } from "react";
import {
  executeWidget,
  fetchWidgetContent,
  type DashboardWidget,
  type WidgetQueryResult,
  type WidgetSize,
} from "../../api/dashboard";
import { getVizComponent } from "./viz";
import type { VizConfig } from "./viz/types";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  folder: string;
  widgets: DashboardWidget[];
  onAddWizard: () => void;
  onEdit: (widget: DashboardWidget) => void;
  onRemove: (widgetId: string) => void;
  onResize: (widget: DashboardWidget, size: WidgetSize) => void | Promise<void>;
  onDesign?: (widget: DashboardWidget) => void;
  onSqlEdit?: (widget: DashboardWidget) => void;
  onAIFix?: (widget: DashboardWidget, error: string) => void;
}

const VIZ_DEFAULT_SIZE: Record<string, WidgetSize> = {
  bar: "md",
  line: "md",
  area: "md",
  pie: "md",
  donut: "md",
  table: "lg",
  kpi: "sm",
};

function effectiveSize(w: DashboardWidget): WidgetSize {
  return w.size ?? VIZ_DEFAULT_SIZE[w.viz_type] ?? "md";
}

type WidgetStatus = "idle" | "running" | "failed";

interface WidgetState {
  result: WidgetQueryResult | null;
  status: WidgetStatus;
  error?: string;
}

const VIZ_TYPE_ICON: Record<string, string> = {
  bar: "\u{1F4CA}",
  line: "\u{1F4C8}",
  area: "\u{1F4C9}",
  pie: "\u{1F967}",
  donut: "\u{1F369}",
  table: "\u{1F4CB}",
  kpi: "\u{1F522}",
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
  if (!iso) return "Never executed";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Never executed";
  const ms = Date.now() - d.getTime();
  const hours = Math.floor(ms / (60 * 60 * 1000));
  if (hours < 1) return "Updated just now";
  if (hours < 24) return `Updated ${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `Updated ${days}d ago`;
}

function parseWidgetContent(raw: string): WidgetQueryResult | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.columns) && Array.isArray(parsed.rows)) {
      return parsed as WidgetQueryResult;
    }
  } catch { /* fall through */ }
  return null;
}

export default function WidgetGrid({ folder, widgets, onAddWizard, onEdit, onRemove, onResize, onDesign, onSqlEdit, onAIFix }: Props) {
  const toast = useToast();
  const [states, setStates] = useState<Record<string, WidgetState>>({});
  const queueTail = useRef<Promise<void>>(Promise.resolve());
  const [queueLength, setQueueLength] = useState(0);
  const dailyFiredFor = useRef<string>("");
  const autoExecutedIds = useRef<Set<string>>(new Set());
  const widgetsRef = useRef(widgets);
  widgetsRef.current = widgets;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const initial: Record<string, WidgetState> = {};
      for (const w of widgets) {
        try {
          const { content } = await fetchWidgetContent(folder, w.id);
          if (cancelled) return;
          initial[w.id] = { result: parseWidgetContent(content), status: "idle" };
        } catch {
          if (cancelled) return;
          initial[w.id] = { result: null, status: "idle" };
        }
      }
      if (!cancelled) setStates(initial);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, widgets.map((w) => w.id).join("|")]);

  const startExecute = useCallback((widget: DashboardWidget): Promise<void> => {
    setStates((s) => ({
      ...s,
      [widget.id]: { ...(s[widget.id] ?? { result: null }), status: "running", error: undefined },
    }));
    return new Promise<void>((resolve) => {
      void (async () => {
        try {
          const { result } = await executeWidget(folder, widget.id);
          if (result.error) {
            setStates((s) => ({
              ...s,
              [widget.id]: {
                result: null,
                status: "failed",
                error: result.error,
              },
            }));
          } else {
            setStates((s) => ({
              ...s,
              [widget.id]: { result, status: "idle" },
            }));
          }
        } catch (e) {
          setStates((s) => ({
            ...s,
            [widget.id]: {
              result: s[widget.id]?.result ?? null,
              status: "failed",
              error: (e as Error).message,
            },
          }));
          toast.error(`Couldn't execute "${widget.title}"`, { detail: (e as Error).message });
        }
        resolve();
      })();
    });
  }, [folder, toast]);

  const enqueueExecute = useCallback((widget: DashboardWidget): void => {
    setStates((s) => ({
      ...s,
      [widget.id]: { ...(s[widget.id] ?? { result: null }), status: "running", error: undefined },
    }));
    setQueueLength((n) => n + 1);
    queueTail.current = queueTail.current
      .catch(() => undefined)
      .then(() => startExecute(widget))
      .finally(() => setQueueLength((n) => Math.max(0, n - 1)));
  }, [startExecute]);

  useEffect(() => {
    const todayKey = new Date().toISOString().slice(0, 10);
    const fireKey = `${folder}|${todayKey}`;
    if (dailyFiredFor.current === fireKey) return;
    dailyFiredFor.current = fireKey;
    const stale = widgetsRef.current.filter(
      (w) => w.refresh === "daily" && !isoIsToday(w.last_refreshed_at),
    );
    for (const w of stale) enqueueExecute(w);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, enqueueExecute]);

  useEffect(() => {
    const widgetIds = new Set(widgets.map((w) => w.id));
    for (const id of autoExecutedIds.current) {
      if (!widgetIds.has(id)) autoExecutedIds.current.delete(id);
    }
    for (const w of widgets) {
      const st = states[w.id];
      if (!st || st.result !== null || st.status !== "idle") continue;
      if (autoExecutedIds.current.has(w.id)) continue;
      if (!w.query) continue;
      autoExecutedIds.current.add(w.id);
      enqueueExecute(w);
    }
  }, [states, widgets, enqueueExecute]);

  const refreshAll = useCallback(() => {
    for (const w of widgets) enqueueExecute(w);
  }, [widgets, enqueueExecute]);

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
                ? `Executing \u2014 ${queueLength} widget${queueLength === 1 ? "" : "s"} left`
                : "Execute every widget query on this dashboard"
            }
          >
            {queueLength > 0 ? `\u21BB Executing\u2026 (${queueLength})` : "\u21BB Refresh all"}
          </button>
          <button
            type="button"
            className="data-dash-action-btn"
            onClick={onAddWizard}
            title="Describe what you want to see and let AI build it"
          >
            + Widget
          </button>
        </div>
      </div>

      {widgets.length === 0 ? (
        <div className="data-dash-hint">
          No widgets yet — click <strong>+ Widget</strong> to describe a chart, table, or KPI.
        </div>
      ) : (
        <div className="data-dash-widget-grid">
          {widgets.map((w) => {
            const state = states[w.id] ?? { result: null, status: "idle" as WidgetStatus };
            const running = state.status === "running";
            const failed = state.status === "failed";
            const size = effectiveSize(w);
            return (
              <article
                key={w.id}
                className={`data-dash-widget data-dash-widget--${w.viz_type} data-dash-widget--size-${size}`}
              >
                <header className="data-dash-widget-head">
                  <span className="data-dash-widget-icon" aria-hidden>
                    {VIZ_TYPE_ICON[w.viz_type] ?? "\u{1F4E6}"}
                  </span>
                  <h3 className="data-dash-widget-title">
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
                      onClick={() => enqueueExecute(w)}
                      disabled={running}
                      title={running ? "Executing\u2026" : "Execute query"}
                    >
                      {running ? "\u2026" : "\u21BB"}
                    </button>
                    {onDesign && (
                      <button
                        type="button"
                        className="data-dash-widget-btn"
                        onClick={() => onDesign(w)}
                        title="Redesign with AI"
                        aria-label="Redesign with AI"
                      >
                        {"\u2728"}
                      </button>
                    )}
                    {onSqlEdit && (
                      <button
                        type="button"
                        className="data-dash-widget-btn"
                        onClick={() => onSqlEdit(w)}
                        title="Edit SQL"
                        aria-label="Edit SQL"
                      >
                        {"\u2699"}
                      </button>
                    )}
                    <button
                      type="button"
                      className="data-dash-widget-btn"
                      onClick={() => onEdit(w)}
                      title="Edit widget"
                      aria-label="Edit widget"
                      >
                        {"\u270E"}
                      </button>
                    <button
                      type="button"
                      className="data-dash-widget-btn"
                      onClick={() => onRemove(w.id)}
                      title="Remove widget"
                    >
                      {"\u00D7"}
                    </button>
                  </div>
                </header>
                <div className="data-dash-widget-body">
                  {failed ? (
                     <WidgetErrorCard
                       error={state.error ?? "Query execution failed"}
                       onRetry={() => enqueueExecute(w)}
                       onSqlEdit={onSqlEdit ? () => onSqlEdit(w) : undefined}
                       onAIFix={onAIFix ? () => onAIFix(w, state.error ?? "Query execution failed") : undefined}
                     />
                  ) : state.result && state.result.rows.length > 0 ? (
                    <WidgetVizRenderer
                      vizType={w.viz_type}
                      result={state.result}
                      config={(w.viz_config as VizConfig) ?? {}}
                    />
                  ) : state.result && state.result.rows.length === 0 ? (
                    <div className="data-dash-hint">Query returned 0 rows.</div>
                  ) : running ? (
                    <div className="data-dash-hint">{"Running first execution\u2026"}</div>
                  ) : (
                    <div className="data-dash-hint">{"Not executed yet \u2014 click \u21BB."}</div>
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

function WidgetVizRenderer({
  vizType,
  result,
  config,
}: {
  vizType: string;
  result: WidgetQueryResult;
  config: VizConfig;
}) {
  const Component = getVizComponent(vizType as never);
  return (
    <Component
      columns={result.columns}
      rows={result.rows}
      config={config}
    />
  );
}

interface ErrorCardProps {
  error: string;
  onRetry: () => void;
  onSqlEdit?: () => void;
  onAIFix?: () => void;
}

function WidgetErrorCard({ error: _error, onRetry, onSqlEdit, onAIFix }: ErrorCardProps) {
  return (
    <div className="widget-error-card">
      <div className="widget-error-card-msg">Something went wrong with this widget.</div>
      <div className="widget-error-card-actions">
        <button
          type="button"
          className="data-dash-action-btn data-dash-action-btn--primary"
          onClick={onRetry}
          title="Re-execute the widget query"
        >
          {"\u21BB Retry"}
        </button>
        {onSqlEdit && (
          <button
            type="button"
            className="data-dash-action-btn"
            onClick={onSqlEdit}
            title="Edit the SQL query manually"
          >
            {"\u2699 Edit query"}
          </button>
        )}
        {onAIFix && (
          <button
            type="button"
            className="data-dash-action-btn"
            onClick={onAIFix}
            title="Let AI fix the query"
          >
            {"\u2728 Ask AI to fix"}
          </button>
        )}
      </div>
    </div>
  );
}
