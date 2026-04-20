import { useCallback, useEffect, useState } from "react";
import { getInsights, type InsightsReport } from "../api";
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

export default function InsightsView() {
  const [days, setDays] = useState<Window>(30);
  const [report, setReport] = useState<InsightsReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (window: Window) => {
    setLoading(true);
    setError(null);
    try {
      const r = await getInsights(window);
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load insights");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(days);
  }, [days, load]);

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
        <button className="insights-btn" onClick={() => void load(days)}>Retry</button>
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
        <div className="insights-empty">
          No sessions in the last {WINDOW_LABELS[days]}. Start chatting to
          populate this view — every turn adds tokens and tool calls here.
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

      {/* Models */}
      {report.models.length > 0 && (
        <section className="insights-section">
          <h3 className="insights-section-title">Models</h3>
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
                return (
                  <tr key={m.model}>
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
            {report.top_sessions.map((s, i) => (
              <li key={i} className="insights-top-session">
                <span className="insights-top-label">{s.label}</span>
                <span className="insights-top-value">{s.value}</span>
                <span className="insights-top-title">{s.title}</span>
                <span className="insights-top-date">{s.date}</span>
              </li>
            ))}
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
