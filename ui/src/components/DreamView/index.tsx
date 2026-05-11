import { useCallback, useEffect, useState } from "react";
import {
  getDreamStatus,
  triggerDream,
  listDreamJournal,
  getDreamJournal,
  listDreamSuggestions,
  acceptDreamSuggestion,
  dismissDreamSuggestion,
  listDreamRuns,
  type DreamStatus,
  type DreamRun,
  type DreamJournalEntry,
  type DreamSuggestion,
} from "../../api/dream";
import MarkdownView from "../MarkdownView";
import "./DreamView.css";

type Tab = "status" | "journal" | "suggestions" | "runs";

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}

export default function DreamView() {
  const [tab, setTab] = useState<Tab>("status");
  const [status, setStatus] = useState<DreamStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [journalEntries, setJournalEntries] = useState<DreamJournalEntry[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [journalContent, setJournalContent] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<DreamSuggestion[]>([]);
  const [runs, setRuns] = useState<DreamRun[]>([]);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await getDreamStatus();
      setStatus(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dream status");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadJournal = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listDreamJournal();
      setJournalEntries(res.entries);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load journal");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSuggestions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listDreamSuggestions();
      setSuggestions(res.suggestions);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load suggestions");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listDreamRuns();
      setRuns(res.runs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load runs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === "status") void loadStatus();
    else if (tab === "journal") void loadJournal();
    else if (tab === "suggestions") void loadSuggestions();
    else if (tab === "runs") void loadRuns();
  }, [tab, loadStatus, loadJournal, loadSuggestions, loadRuns]);

  const handleTrigger = useCallback(async (depth: string) => {
    setTriggering(true);
    setError(null);
    try {
      await triggerDream(depth);
      await loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to trigger dream");
    } finally {
      setTriggering(false);
    }
  }, [loadStatus]);

  const handleSelectDate = useCallback(async (date: string) => {
    setSelectedDate(date);
    try {
      const res = await getDreamJournal(date);
      setJournalContent(res.content);
    } catch {
      setJournalContent(null);
    }
  }, []);

  const handleAccept = useCallback(async (filename: string) => {
    try {
      await acceptDreamSuggestion(filename);
      await loadSuggestions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to accept suggestion");
    }
  }, [loadSuggestions]);

  const handleDismiss = useCallback(async (filename: string) => {
    try {
      await dismissDreamSuggestion(filename);
      await loadSuggestions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to dismiss suggestion");
    }
  }, [loadSuggestions]);

  const tabs: { id: Tab; label: string }[] = [
    { id: "status", label: "Status" },
    { id: "journal", label: "Journal" },
    { id: "suggestions", label: "Suggestions" },
    { id: "runs", label: "History" },
  ];

  return (
    <div className="dm-view">
      <div className="dm-header">
        <h2>Dream</h2>
        {status && (
          <span className="dm-status-pill">
            <span className={`dm-status-dot dm-status-dot--${status.running ? "running" : "idle"}`} />
            {status.running ? "Running" : "Idle"}
          </span>
        )}
        {status?.enabled && !status.running && (
          <button
            className="dm-btn dm-btn--primary"
            disabled={triggering}
            onClick={() => void handleTrigger("light")}
          >
            {triggering ? "Starting…" : "Trigger Dream"}
          </button>
        )}
      </div>

      <div className="dm-tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`dm-tab${tab === t.id ? " dm-tab--active" : ""}`}
            onClick={() => { setTab(t.id); setError(null); }}
          >
            {t.label}
            {t.id === "suggestions" && suggestions.length > 0 && ` (${suggestions.length})`}
          </button>
        ))}
      </div>

      {error && (
        <div className="dm-error-banner">{error}</div>
      )}

      <div className="dm-view-scroll">
        {loading && !status && tab === "status" && (
          <div className="dm-loading"><span className="dm-spin" />Loading…</div>
        )}

        {tab === "status" && status && <StatusTab status={status} onTrigger={handleTrigger} triggering={triggering} />}
        {tab === "journal" && <JournalTab entries={journalEntries} selectedDate={selectedDate} content={journalContent} onSelect={handleSelectDate} />}
        {tab === "suggestions" && <SuggestionsTab suggestions={suggestions} onAccept={handleAccept} onDismiss={handleDismiss} />}
        {tab === "runs" && <RunsTab runs={runs} />}
      </div>
    </div>
  );
}

function StatusTab({ status, onTrigger, triggering }: { status: DreamStatus; onTrigger: (d: string) => void; triggering: boolean }) {
  const budgetPct = status.budget.daily_limit > 0
    ? Math.min(100, (status.budget.used_today / status.budget.daily_limit) * 100)
    : 0;
  const budgetClass = budgetPct > 90 ? "dm-budget-fill--full" : budgetPct > 60 ? "dm-budget-fill--warn" : "";

  return (
    <>
      {!status.enabled && (
        <div className="dm-empty">
          Dream system is disabled. Enable it in <code>~/.nexus/config.toml</code>.
        </div>
      )}

      {status.enabled && (
        <>
          <div className="dm-card">
            <div className="dm-card-header">
              <span className="dm-card-title">Budget</span>
              <span className="dm-card-meta">
                {formatTokens(status.budget.used_today)} / {formatTokens(status.budget.daily_limit)} tokens today
              </span>
            </div>
            <div className="dm-budget-bar">
              <div className={`dm-budget-fill ${budgetClass}`} style={{ width: `${budgetPct}%` }} />
            </div>
          </div>

          {status.last_run && (
            <div className="dm-card">
              <div className="dm-card-header">
                <span className="dm-card-title">Last Run</span>
                <span className="dm-card-meta">{relTime(status.last_run.started_at)}</span>
              </div>
              <div className="dm-card-body">
                <div>Depth: {status.last_run.depth} · Duration: {formatDuration(status.last_run.duration_ms)}</div>
                <div>Tokens: {formatTokens(status.last_run.tokens_in)} in / {formatTokens(status.last_run.tokens_out)} out</div>
                <div>Merges: {status.last_run.memories_merged} · Insights: {status.last_run.insights_generated}</div>
                {status.last_run.error && <div style={{ color: "var(--bad)" }}>Error: {status.last_run.error}</div>}
                <div className="dm-run-phases">
                  {status.last_run.phases_run.split(",").map((p) => (
                    <span key={p} className="dm-phase-tag">{p.trim()}</span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {!status.running && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {(["light", "medium", "deep"] as const).map((d) => (
                <button
                  key={d}
                  className="dm-btn"
                  disabled={triggering || budgetPct >= 100}
                  onClick={() => onTrigger(d)}
                >
                  {d.charAt(0).toUpperCase() + d.slice(1)}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function JournalTab({ entries, selectedDate, content, onSelect }: { entries: DreamJournalEntry[]; selectedDate: string | null; content: string | null; onSelect: (d: string) => void }) {
  if (entries.length === 0) {
    return <div className="dm-empty">No dream journal entries yet.</div>;
  }

  return (
    <>
      {entries.map((e) => (
        <button
          key={e.date}
          className={`dm-card${selectedDate === e.date ? " dm-card--selected" : ""}`}
          style={{ cursor: "pointer", textAlign: "left", width: "100%" }}
          onClick={() => onSelect(e.date)}
        >
          <div className="dm-card-header">
            <span className="dm-card-title">{e.date}</span>
            <span className="dm-card-meta">{(e.size / 1024).toFixed(1)} KB</span>
          </div>
        </button>
      ))}
      {selectedDate && content && (
        <div className="dm-card">
          <div className="dm-card-header">
            <span className="dm-card-title">{selectedDate}</span>
            <button className="dm-btn" onClick={() => onSelect(selectedDate)}>Refresh</button>
          </div>
          <MarkdownView>{content}</MarkdownView>
        </div>
      )}
    </>
  );
}

function SuggestionsTab({ suggestions, onAccept, onDismiss }: { suggestions: DreamSuggestion[]; onAccept: (f: string) => void; onDismiss: (f: string) => void }) {
  if (suggestions.length === 0) {
    return <div className="dm-empty">No pending skill suggestions.</div>;
  }

  return (
    <>
      {suggestions.map((s) => (
        <div key={s.filename} className="dm-card">
          <div className="dm-card-header">
            <span className="dm-card-title">{s.name}</span>
          </div>
          {s.description && <div className="dm-card-body">{s.description}</div>}
          <div className="dm-suggestion-actions">
            <button className="dm-btn dm-btn--primary" onClick={() => onAccept(s.filename)}>
              Accept
            </button>
            <button className="dm-btn dm-btn--danger" onClick={() => onDismiss(s.filename)}>
              Dismiss
            </button>
          </div>
        </div>
      ))}
    </>
  );
}

function RunsTab({ runs }: { runs: DreamRun[] }) {
  if (runs.length === 0) {
    return <div className="dm-empty">No dream runs recorded.</div>;
  }

  return (
    <>
      {runs.map((r) => (
        <div key={r.id} className="dm-card">
          <div className="dm-card-header">
            <span className="dm-card-title">Run #{r.id}</span>
            <span className="dm-card-meta">
              {r.status === "running" ? "Running" : relTime(r.started_at)}
            </span>
          </div>
          <div className="dm-card-body">
            <div>Depth: {r.depth} · Duration: {formatDuration(r.duration_ms)}</div>
            <div>Tokens: {formatTokens(r.tokens_in)} in / {formatTokens(r.tokens_out)} out</div>
            <div>Merges: {r.memories_merged} · Insights: {r.insights_generated}</div>
            {r.error && <div style={{ color: "var(--bad)" }}>{r.error}</div>}
            <div className="dm-run-phases">
              {r.phases_run.split(",").map((p) => (
                <span key={p} className="dm-phase-tag">{p.trim()}</span>
              ))}
            </div>
          </div>
        </div>
      ))}
    </>
  );
}
