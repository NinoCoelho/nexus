/**
 * ReindexModal — full GraphRAG reindex progress dialog.
 *
 * Streams reindex progress via SSE from POST /graphrag/reindex.
 * Shows per-file indexing status, entity/triple counts, and a
 * progress bar. Supports both incremental (skip unchanged files)
 * and full (drop + rebuild) modes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  graphragReindex,
  type ReindexEvent,
  type ReindexFileEvent,
  type ReindexStatsEvent,
} from "../api";
import "./ReindexModal.css";

interface Props {
  open: boolean;
  onClose: () => void;
}

interface LogEntry {
  ts: number;
  text: string;
  kind: "info" | "file" | "skip" | "error";
}

export default function ReindexModal({ open, onClose }: Props) {
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [progress, setProgress] = useState<ReindexFileEvent | null>(null);
  const [finalStats, setFinalStats] = useState<ReindexStatsEvent | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  useEffect(() => {
    if (!open) {
      abortRef.current?.abort();
      setRunning(false);
      setDone(false);
      setLogs([]);
      setProgress(null);
      setFinalStats(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !running) onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, running, onClose]);

  const addLog = useCallback((text: string, kind: LogEntry["kind"] = "info") => {
    setLogs((prev) => [...prev, { ts: Date.now(), text, kind }]);
  }, []);

  const startReindex = useCallback(
    async (full: boolean) => {
      setRunning(true);
      setDone(false);
      setLogs([]);
      setProgress(null);
      setFinalStats(null);
      const ac = new AbortController();
      abortRef.current = ac;

      try {
        await graphragReindex(
          (e: ReindexEvent) => {
            switch (e.type) {
              case "status":
                addLog(e.message);
                break;
              case "file": {
                const fe = e as ReindexFileEvent & { skipped?: boolean };
                setProgress(fe);
                if (fe.skipped) {
                  addLog(`Skipped ${fe.path} (unchanged)`, "skip");
                } else {
                  addLog(`Indexed ${fe.path}`, "file");
                }
                break;
              }
              case "error":
                addLog(`Error: ${e.path ? e.path + ": " : ""}${e.detail}`, "error");
                break;
              case "stats": {
                const se = e as ReindexStatsEvent;
                setFinalStats(se);
                addLog(
                  `Done — ${se.files_indexed} indexed, ${se.files_skipped} skipped, ` +
                    `${se.entities} entities, ${se.triples} triples (${se.elapsed_s}s)`,
                );
                break;
              }
              case "done":
                setDone(true);
                break;
            }
          },
          ac.signal,
          full,
        );
      } catch (e) {
        addLog(e instanceof Error ? e.message : "Reindex failed", "error");
        setDone(true);
      } finally {
        setRunning(false);
      }
    },
    [addLog],
  );

  if (!open) return null;

  const idle = !running && !done;

  return (
    <div className="modal-backdrop" onClick={running ? undefined : onClose}>
      <div className="reindex-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="reindex-header">
          <span className="reindex-title">GraphRAG Index</span>
          {!running && (
            <button className="reindex-close" onClick={onClose} aria-label="Close">
              ✕
            </button>
          )}
        </div>

        {idle && (
          <div className="reindex-choices">
            <button
              className="reindex-choice-card"
              onClick={() => startReindex(false)}
            >
              <div className="reindex-choice-title">Incremental Update</div>
              <div className="reindex-choice-desc">
                Index only new or changed files. Fast — skips files with matching
                content hashes.
              </div>
            </button>
            <button
              className="reindex-choice-card reindex-choice-card--danger"
              onClick={() => startReindex(true)}
            >
              <div className="reindex-choice-title">Full Reindex</div>
              <div className="reindex-choice-desc">
                Drop all data and rebuild from scratch. Use when the ontology
                changed or the index is corrupt.
              </div>
            </button>
          </div>
        )}

        {finalStats && (
          <div className="reindex-stats-grid">
            <div className="reindex-stat-card">
              <div className="reindex-stat-value">{finalStats.files_indexed}</div>
              <div className="reindex-stat-label">Indexed</div>
            </div>
            <div className="reindex-stat-card">
              <div className="reindex-stat-value reindex-stat-value--dim">
                {finalStats.files_skipped}
              </div>
              <div className="reindex-stat-label">Skipped</div>
            </div>
            <div className="reindex-stat-card">
              <div className="reindex-stat-value">{finalStats.entities}</div>
              <div className="reindex-stat-label">Entities</div>
            </div>
            <div className="reindex-stat-card">
              <div className="reindex-stat-value">{finalStats.triples}</div>
              <div className="reindex-stat-label">Relations</div>
            </div>
            <div className="reindex-stat-card">
              <div className="reindex-stat-value">{finalStats.elapsed_s}s</div>
              <div className="reindex-stat-label">Elapsed</div>
            </div>
          </div>
        )}

        {running && progress && !finalStats && (
          <div className="reindex-progress-bar-wrap">
            <div className="reindex-progress-track">
              <div
                className="reindex-progress-fill"
                style={{
                  width: `${progress.files_total > 0 ? (progress.files_done / progress.files_total) * 100 : 0}%`,
                }}
              />
            </div>
            <div className="reindex-progress-text">
              {progress.files_done}/{progress.files_total} files — {progress.entities}{" "}
              entities
            </div>
          </div>
        )}

        {(running || done) && (
          <div className="reindex-log">
            {logs.map((l, i) => (
              <div key={i} className={`reindex-log-line reindex-log-${l.kind}`}>
                {l.text}
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}

        <div className="reindex-actions">
          {running && (
            <button
              className="modal-btn modal-btn--danger"
              onClick={() => abortRef.current?.abort()}
            >
              Cancel
            </button>
          )}
          {done && (
            <button className="modal-btn" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
