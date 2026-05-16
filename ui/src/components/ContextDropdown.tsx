import { useCallback, useEffect, useRef, useState } from "react";
import type { ContextStats, CompactResult } from "../api/sessions";
import { getContextStats } from "../api/sessions";
import "./ContextDropdown.css";

interface Props {
  sessionId: string | null;
  onCompact: (options?: { strategy?: string; force_summarize?: boolean }) => Promise<CompactResult | undefined>;
  compacting?: boolean;
  polledPct?: number;
}

const ZONE_COLORS: Record<string, string> = {
  green: "#22c55e",
  yellow: "#eab308",
  orange: "#f97316",
  red: "#ef4444",
  unknown: "#888",
};

function fmtTok(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export default function ContextDropdown({ sessionId, onCompact, compacting, polledPct }: Props) {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState<ContextStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CompactResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const btnRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const loadStats = useCallback(async () => {
    if (!sessionId) return;
    try {
      const s = await getContextStats(sessionId);
      setStats(s);
    } catch {
      /* ignore */
    }
  }, [sessionId]);

  const toggle = useCallback(() => {
    if (open) {
      setOpen(false);
      setResult(null);
      setError(null);
      return;
    }
    setOpen(true);
    setResult(null);
    setError(null);
    void loadStats();
  }, [open, loadStats]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      if (
        panelRef.current &&
        !panelRef.current.contains(e.target as Node) &&
        btnRef.current &&
        !btnRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
        setResult(null);
        setError(null);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const doCompact = useCallback(
    async (strategy: string) => {
      if (!sessionId || compacting) return;
      setLoading(true);
      setError(null);
      try {
        const r = await onCompact({ strategy, force_summarize: true });
        if (r) {
          setResult(r);
          await loadStats();
        }
      } catch {
        setError("Compact failed.");
      } finally {
        setLoading(false);
      }
    },
    [sessionId, compacting, onCompact, loadStats],
  );

  const zone = stats?.context_zone ?? "unknown";
  const displayPct = polledPct != null ? polledPct : Math.round((stats?.context_pct ?? 0) * 100);

  return (
    <>
      <div
        ref={btnRef}
        className="ctx-dropdown-trigger"
        onClick={toggle}
        role="button"
        tabIndex={0}
        aria-expanded={open}
      >
        {displayPct}%
      </div>
      {open && (
        <div
          ref={panelRef}
          className="ctx-dropdown-panel"
          role="dialog"
          aria-label="Context usage"
        >
          {!stats ? (
            <div className="ctx-dropdown-loading">Loading...</div>
          ) : (
            <>
              <div className="ctx-dropdown-header">
                <span className="ctx-dropdown-title">Context</span>
                <span className="ctx-dropdown-zone" style={{ color: ZONE_COLORS[zone] }}>
                  {zone}
                </span>
              </div>

              <div className="ctx-dropdown-gauge">
                <div className="ctx-dropdown-gauge-track">
                  <div
                    className="ctx-dropdown-gauge-fill"
                    style={{
                      width: `${Math.min(displayPct, 100)}%`,
                      background: ZONE_COLORS[zone],
                    }}
                  />
                  <div
                    className="ctx-dropdown-gauge-marker"
                    style={{ left: "60%", borderColor: ZONE_COLORS.yellow }}
                  />
                  <div
                    className="ctx-dropdown-gauge-marker"
                    style={{ left: "80%", borderColor: ZONE_COLORS.orange }}
                  />
                  <div
                    className="ctx-dropdown-gauge-marker"
                    style={{ left: "90%", borderColor: ZONE_COLORS.red }}
                  />
                </div>
                <div className="ctx-dropdown-gauge-label">
                  {fmtTok(stats.estimated_context_tokens)} / {fmtTok(stats.context_window_tokens)} tokens
                </div>
              </div>

              <div className="ctx-dropdown-breakdown">
                {(["user", "assistant", "tool", "system"] as const).map((role) => {
                  const tok = stats.token_breakdown[role] ?? 0;
                  const cnt = stats.message_counts[role] ?? 0;
                  if (tok === 0 && cnt === 0) return null;
                  return (
                    <div key={role} className="ctx-dropdown-stat-row">
                      <span className="ctx-dropdown-stat-label">{role}</span>
                      <span className="ctx-dropdown-stat-value">
                        {fmtTok(tok)} tokens &middot; {cnt} msgs
                      </span>
                    </div>
                  );
                })}
              </div>

              {stats.tool_stats.length > 0 && (
                <div className="ctx-dropdown-tools">
                  <div className="ctx-dropdown-section-title">Tools</div>
                  {stats.tool_stats.slice(0, 6).map((t) => (
                    <div key={t.name} className="ctx-dropdown-stat-row">
                      <span className="ctx-dropdown-stat-label">{t.name}</span>
                      <span className="ctx-dropdown-stat-value">
                        {fmtTok(t.estimated_tokens)} &middot; {t.call_count}x
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <div className="ctx-dropdown-actions">
                <div className="ctx-dropdown-section-title">Compact</div>
                <div className="ctx-dropdown-btn-row">
                  <button
                    type="button"
                    className="ctx-btn ctx-btn-primary"
                    disabled={loading || compacting}
                    onClick={() => void doCompact("auto")}
                  >
                    Compact
                  </button>
                  <button
                    type="button"
                    className="ctx-btn"
                    disabled={loading || compacting}
                    onClick={() => void doCompact("summarize_only")}
                    title="Summarize older turns into a compressed memory"
                  >
                    Summarize
                  </button>
                  <button
                    type="button"
                    className="ctx-btn ctx-btn-danger"
                    disabled={loading || compacting}
                    onClick={() => void doCompact("aggressive")}
                    title="Maximum compaction: lower thresholds + summarization"
                  >
                    Aggressive
                  </button>
                </div>
              </div>

              {result && (
                <div className={`ctx-dropdown-result ${result.still_overflowed ? "ctx-dropdown-result-warn" : "ctx-dropdown-result-ok"}`}>
                  {result.summarized
                    ? `Summarized ${result.summarized_messages} turns. ${fmtTok(result.tokens_before)} → ${fmtTok(result.tokens_after)} tokens.`
                    : result.compacted > 0
                      ? `Compacted ${result.compacted} tool results, saved ${Math.round(result.saved_bytes / 1024)} KB.`
                      : "Nothing to compact."}
                  {result.still_overflowed && " Still tight — consider starting a new session."}
                </div>
              )}
              {error && <div className="ctx-dropdown-result ctx-dropdown-result-warn">{error}</div>}
            </>
          )}
        </div>
      )}
    </>
  );
}
