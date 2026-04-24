/**
 * InsightsView — analytics dashboard for session history.
 *
 * Fetches a report from GET /insights and renders:
 *   - Overview cards (sessions, messages, tokens, estimated cost)
 *   - Routing mini-card (current mode + classifier)
 *   - Model breakdown table (click a row to scope the rest of the dashboard)
 *   - Top tools by call count
 *   - Activity heatmap by day-of-week and hour
 *   - Notable sessions (click to open in chat)
 *
 * Time windows: 7 / 30 / 90 / 365 days.
 */

import { useCallback, useEffect, useState } from "react";
import { getInsights, getRouting, type InsightsReport, type RoutingConfig } from "../api";
import "./InsightsView.css";

type Window = 7 | 30 | 90 | 365;

const WINDOW_LABELS: Record<Window, string> = {
  7: "7 days",
  30: "30 days",
  90: "90 days",
  365: "1 year",
};

function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

function formatCost(cost: number): string {
  if (cost === 0) return "$0.00";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

interface Props {
  onOpenSession?: (sessionId: string) => void;
}

export default function InsightsView({ onOpenSession }: Props) {
  const [days, setDays] = useState<Window>(30);
  const [modelFilter, setModelFilter] = useState<string | null>(null);
  const [report, setReport] = useState<InsightsReport | null>(null);
  const [routing, setRouting] = useState<RoutingConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (window: Window, model: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const [r, rc] = await Promise.all([
        getInsights(window, model ?? undefined),
        getRouting().catch(() => null),
      ]);
      setReport(r);
      setRouting(rc);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load insights");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(days, modelFilter);
  }, [days, modelFilter, load]);

  if (loading && !report) {
    return (
      <div className="insights-view">
        <div className="insights-loading">Loading insights…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="insights-view">
        <div className="insights-error">{error}</div>
        <button className="insights-btn" onClick={() => void load(days, modelFilter)}>Retry</button>
      </div>
    );
  }

  if (!report || report.empty) {
    return (
      <div className="insights-view">
        <div className="insights-header">
          <h2 className="insights-title">Insights</h2>
          <WindowSelect days={days} onChange={setDays} />
        </div>
        {modelFilter && (
          <FilterPill label={modelFilter} onClear={() => setModelFilter(null)} />
        )}
        <div className="insights-empty">
          No sessions in the last {WINDOW_LABELS[days]}
          {modelFilter ? ` for ${modelFilter}` : ""}. Start chatting to populate this view.
        </div>
      </div>
    );
  }

  const o = report.overview;
  const tokenPeakDay = Math.max(...report.activity.by_day.map((d) => d.count), 1);

  return (
    <div className="insights-view">
      <div className="insights-header">
        <h2 className="insights-title">Insights</h2>
        <WindowSelect days={days} onChange={setDays} />
      </div>

      {modelFilter && (
        <FilterPill label={modelFilter} onClear={() => setModelFilter(null)} />
      )}

      {/* Top-row metric tiles */}
      <div className="insights-tiles">
        <Tile label="Sessions" value={o.total_sessions.toLocaleString()} />
        <Tile label="Messages" value={o.total_messages.toLocaleString()} />
        <Tile
          label="Tokens"
          value={o.total_tokens.toLocaleString()}
          hint={`${o.total_input_tokens.toLocaleString()} in · ${o.total_output_tokens.toLocaleString()} out`}
        />
        <Tile
          label="Est. cost"
          value={formatCost(o.estimated_cost_usd)}
          hint={
            o.sessions_unpriced > 0
              ? `${o.sessions_unpriced} without pricing`
              : "all models priced"
          }
        />
        <Tile
          label="Active time"
          value={formatDuration(o.total_active_seconds)}
          hint={`avg ${formatDuration(o.avg_session_duration)}`}
        />
      </div>

      {/* Routing mini-card: current mode (config) + top auto pick (from models breakdown) */}
      {routing && (
        <section className="insights-section insights-routing-card">
          <div>
            <span className="insights-routing-label">Routing</span>
            <span className={`insights-routing-mode insights-routing-mode--${routing.routing_mode}`}>
              {routing.routing_mode}
            </span>
            {routing.routing_mode === "auto" && (
              <span className="insights-routing-hint">
                classifier: built-in
              </span>
            )}
            {routing.routing_mode === "fixed" && routing.default_model && (
              <span className="insights-routing-hint">
                default: {routing.default_model.split("/").pop()}
              </span>
            )}
          </div>
          {report.models.length > 0 && (
            <div className="insights-routing-hint">
              most-used in window: <strong>{(report.models[0].model || "").split("/").pop() || "—"}</strong>
            </div>
          )}
        </section>
      )}

      {/* Models */}
      {report.models.length > 0 && (
        <section className="insights-section">
          <h3 className="insights-section-title">Models — click to filter</h3>
          <table className="insights-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Sessions</th>
                <th>Tokens</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {report.models.map((m) => {
                const name = m.model.includes("/") ? m.model.split("/").pop() : m.model;
                const active = modelFilter === m.model;
                return (
                  <tr
                    key={m.model}
                    className={`insights-table-row${active ? " insights-table-row--active" : ""}`}
                    onClick={() => setModelFilter(active ? null : m.model)}
                    style={{ cursor: "pointer" }}
                  >
                    <td title={m.model}>{name}</td>
                    <td className="num">{m.sessions}</td>
                    <td className="num">{m.total_tokens.toLocaleString()}</td>
                    <td className="num">
                      {m.has_pricing ? formatCost(m.cost_usd) : <span className="dim">N/A</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {/* Tools */}
      {report.tools.length > 0 && (
        <section className="insights-section">
          <h3 className="insights-section-title">Top tools</h3>
          <div className="insights-tools">
            {report.tools.slice(0, 12).map((t) => (
              <div key={t.tool} className="insights-tool-row">
                <span className="insights-tool-name">{t.tool}</span>
                <div className="insights-tool-bar">
                  <div
                    className="insights-tool-bar-fill"
                    style={{ width: `${Math.max(2, t.percentage)}%` }}
                  />
                </div>
                <span className="insights-tool-count">{t.count}</span>
                <span className="insights-tool-pct">{t.percentage.toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Activity histogram */}
      <section className="insights-section">
        <h3 className="insights-section-title">Activity by day of week</h3>
        <div className="insights-histogram">
          {report.activity.by_day.map((d) => (
            <div key={d.day} className="insights-hist-col">
              <div
                className="insights-hist-bar"
                style={{ height: `${(d.count / tokenPeakDay) * 100}%` }}
                title={`${d.day}: ${d.count} sessions`}
              />
              <div className="insights-hist-label">{d.day}</div>
              <div className="insights-hist-value">{d.count}</div>
            </div>
          ))}
        </div>
        <div className="insights-activity-meta">
          <span>Active days: <strong>{report.activity.active_days}</strong></span>
          {report.activity.max_streak > 1 && (
            <span>Best streak: <strong>{report.activity.max_streak} days</strong></span>
          )}
          {report.activity.busiest_hour && (
            <span>
              Peak hour: <strong>
                {(() => {
                  const h = report.activity.busiest_hour!.hour;
                  const ampm = h < 12 ? "AM" : "PM";
                  const disp = h % 12 || 12;
                  return `${disp}${ampm}`;
                })()}
              </strong>
            </span>
          )}
        </div>
      </section>

      {/* Notable sessions */}
      {report.top_sessions.length > 0 && (
        <section className="insights-section">
          <h3 className="insights-section-title">Notable sessions</h3>
          <ul className="insights-top-sessions">
            {report.top_sessions.map((s, i) => {
              const clickable = !!onOpenSession && !!s.session_id;
              return (
                <li
                  key={i}
                  className={`insights-top-session${clickable ? " insights-top-session--clickable" : ""}`}
                  onClick={clickable ? () => onOpenSession!(s.session_id) : undefined}
                  role={clickable ? "button" : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  onKeyDown={clickable ? (e) => { if (e.key === "Enter") onOpenSession!(s.session_id); } : undefined}
                  title={clickable ? "Open session in chat" : undefined}
                >
                  <span className="insights-top-label">{s.label}</span>
                  <span className="insights-top-value">{s.value}</span>
                  <span className="insights-top-title">{s.title}</span>
                  <span className="insights-top-date">{s.date}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="insights-tile">
      <div className="insights-tile-label">{label}</div>
      <div className="insights-tile-value">{value}</div>
      {hint && <div className="insights-tile-hint">{hint}</div>}
    </div>
  );
}

function WindowSelect({ days, onChange }: { days: Window; onChange: (d: Window) => void }) {
  const options: Window[] = [7, 30, 90, 365];
  return (
    <div className="insights-window-select">
      {options.map((d) => (
        <button
          key={d}
          className={`insights-window-btn${d === days ? " insights-window-btn--active" : ""}`}
          onClick={() => onChange(d)}
          type="button"
        >
          {WINDOW_LABELS[d]}
        </button>
      ))}
    </div>
  );
}

function FilterPill({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <div className="insights-filter-pill">
      <span>Filtered by model:</span>
      <strong>{label}</strong>
      <button type="button" className="insights-filter-clear" onClick={onClear} aria-label="Clear filter">
        ✕
      </button>
    </div>
  );
}
