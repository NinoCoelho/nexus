/**
 * Blocking progress UI shown while a folder index is being built.
 *
 * Streams events from /graph/folder/index. Surfaces phase labels including
 * `loading-embedder` so the ~10s warmup doesn't look frozen.
 */

import { useEffect, useRef, useState } from "react";
import {
  indexFolderStream,
  type FolderIndexEvent,
  type FolderOntology,
} from "../../api/folderGraph";

interface Props {
  folderPath: string;
  folderLabel: string;
  full?: boolean;
  /** Only needed on first build before ontology is persisted. */
  ontology?: FolderOntology;
  onDone: () => void;
  onCancel?: () => void;
  onError?: (detail: string) => void;
}

const PHASE_LABELS: Record<string, string> = {
  "loading-embedder": "Loading embedding model…",
  scanning: "Scanning folder…",
  extracting: "Extracting entities…",
  writing: "Writing graph…",
};

export function FolderIndexProgress({
  folderPath,
  folderLabel,
  full,
  ontology,
  onDone,
  onCancel,
  onError,
}: Props) {
  const [phase, setPhase] = useState<string>("loading-embedder");
  const [statusMsg, setStatusMsg] = useState<string>("");
  const [filesDone, setFilesDone] = useState(0);
  const [filesTotal, setFilesTotal] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  const [latestPath, setLatestPath] = useState<string>("");
  const [doneStats, setDoneStats] = useState<{ entities: number; triples: number; elapsed_s: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const triggeredOnDone = useRef(false);

  useEffect(() => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let cancelled = false;
    let stats: { entities: number; triples: number; elapsed_s: number } | null = null;

    (async () => {
      try {
        await indexFolderStream(
          folderPath,
          (e: FolderIndexEvent) => {
            if (cancelled) return;
            if (e.type === "phase") setPhase(e.phase);
            else if (e.type === "status") setStatusMsg(e.message);
            else if (e.type === "file") {
              setFilesDone(e.files_done);
              setFilesTotal(e.files_total);
              setLatestPath(e.path);
            } else if (e.type === "error") {
              setErrors((errs) => [...errs, e.path ? `${e.path}: ${e.detail}` : e.detail]);
              if (!e.path) onError?.(e.detail);
            } else if (e.type === "stats") {
              stats = { entities: e.entities, triples: e.triples, elapsed_s: e.elapsed_s };
              setDoneStats(stats);
            } else if (e.type === "done") {
              triggeredOnDone.current = true;
              onDone();
            }
          },
          { full, ontology, signal: ctrl.signal },
        );
      } catch (err) {
        if (cancelled || (err instanceof Error && err.name === "AbortError")) return;
        const msg = err instanceof Error ? err.message : String(err);
        onError?.(msg);
      }
    })();

    return () => {
      cancelled = true;
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folderPath]);

  function handleCancel() {
    abortRef.current?.abort();
    onCancel?.();
  }

  const pct = filesTotal > 0 ? Math.round((filesDone / filesTotal) * 100) : null;

  return (
    <div className="fg-progress">
      <div className="fg-progress-card">
        <div className="fg-progress-title">
          Building graph for <span className="fg-folder-name">{folderLabel}</span>
        </div>
        <div className="fg-progress-phase">{PHASE_LABELS[phase] ?? phase}</div>
        {statusMsg && <div className="fg-progress-status">{statusMsg}</div>}

        {filesTotal > 0 && (
          <div className="fg-progress-bar-wrap">
            <div className="fg-progress-bar">
              <div
                className="fg-progress-bar-fill"
                style={{ width: `${pct ?? 0}%` }}
              />
            </div>
            <div className="fg-progress-counts">
              {filesDone} / {filesTotal} files {pct !== null && <>({pct}%)</>}
            </div>
            {latestPath && <div className="fg-progress-current">{latestPath}</div>}
          </div>
        )}

        {doneStats && (
          <div className="fg-progress-stats">
            {doneStats.entities} entities · {doneStats.triples} relations ·{" "}
            {doneStats.elapsed_s.toFixed(1)}s
          </div>
        )}

        {errors.length > 0 && (
          <details className="fg-progress-errors">
            <summary>{errors.length} file error(s)</summary>
            <ul>
              {errors.slice(0, 20).map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </details>
        )}

        <div className="fg-progress-footer">
          {!doneStats && (
            <button
              type="button"
              className="fg-btn fg-btn--ghost"
              onClick={handleCancel}
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
