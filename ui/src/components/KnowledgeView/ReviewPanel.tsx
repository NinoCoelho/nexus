/**
 * Fact review panel — bitemporal-lite conflict queue + entity merge history.
 *
 * Contradicting facts detected during GraphRAG indexing land here as "pending"
 * instead of going live. Each card shows the old claim vs the new claim with
 * sources and validity, and offers: approve new (supersedes old), reject new,
 * or keep both. Recent entity merges are listed with undo.
 */

import { useCallback, useEffect, useState } from "react";
import {
  getKnowledgeReview,
  resolveKnowledgeConflict,
  unmergeKnowledgeEntities,
  type EntityMerge,
  type KnowledgeConflict,
} from "../../api";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import { useToast } from "../../toast/ToastProvider";

function validityLabel(t: { valid_from: string | null; valid_to: string | null }): string {
  if (!t.valid_from && !t.valid_to) return "";
  if (t.valid_from && t.valid_to) return `${t.valid_from} → ${t.valid_to}`;
  if (t.valid_from) return `since ${t.valid_from}`;
  return `until ${t.valid_to}`;
}

function ConflictCard({
  conflict,
  onResolved,
}: {
  conflict: KnowledgeConflict;
  onResolved: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const resolve = async (resolution: "approve_new" | "reject_new" | "keep_both") => {
    setBusy(true);
    try {
      await resolveKnowledgeConflict(conflict.id, resolution);
      toast.success("Conflict resolved");
      onResolved();
    } catch (e) {
      toast.error("Failed to resolve conflict", { detail: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const a = conflict.triple_a;
  const b = conflict.triple_b;

  return (
    <div className="kv-review-card">
      <div className="kv-review-card-head">
        <span className="kv-review-relation">
          {conflict.head} <em>{conflict.relation}</em>
        </span>
        <span className="kv-review-kind">{conflict.kind.replace(/_/g, " ")}</span>
      </div>
      <div className="kv-review-claims">
        <div className="kv-review-claim kv-review-claim--old">
          <div className="kv-review-claim-value">{conflict.old_tail}</div>
          <div className="kv-review-claim-meta">
            <span className="kv-review-source" title={a.source_path}>
              {a.source_path || "unknown source"}
            </span>
            {validityLabel(a) && <span className="kv-review-validity">{validityLabel(a)}</span>}
          </div>
        </div>
        <div className="kv-review-vs">vs</div>
        <div className="kv-review-claim kv-review-claim--new">
          <div className="kv-review-claim-value">{conflict.new_tail}</div>
          <div className="kv-review-claim-meta">
            <span className="kv-review-source" title={b.source_path}>
              {b.source_path || "unknown source"}
            </span>
            {validityLabel(b) && <span className="kv-review-validity">{validityLabel(b)}</span>}
          </div>
        </div>
      </div>
      <div className="kv-review-actions">
        <button
          className="kv-review-btn kv-review-btn--approve"
          disabled={busy}
          onClick={() => void resolve("approve_new")}
        >
          Approve new
        </button>
        <button
          className="kv-review-btn kv-review-btn--reject"
          disabled={busy}
          onClick={() => void resolve("reject_new")}
        >
          Reject new
        </button>
        <button
          className="kv-review-btn kv-review-btn--keep"
          disabled={busy}
          onClick={() => void resolve("keep_both")}
        >
          Keep both
        </button>
      </div>
    </div>
  );
}

function MergeRow({ merge, onUndone }: { merge: EntityMerge; onUndone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const undo = async () => {
    setBusy(true);
    try {
      await unmergeKnowledgeEntities(merge.id);
      toast.success(`Restored “${merge.merged_name}”`);
      onUndone();
    } catch (e) {
      toast.error("Failed to undo merge", { detail: String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kv-review-merge">
      <span className="kv-review-merge-label">
        “{merge.merged_name}” merged into “{merge.survivor_name ?? "?"}”
      </span>
      <button className="kv-review-btn kv-review-btn--keep" disabled={busy} onClick={() => void undo()}>
        Undo
      </button>
    </div>
  );
}

export function ReviewPanel({
  onClose,
  onCountChange,
}: {
  onClose: () => void;
  onCountChange?: (count: number) => void;
}) {
  const [conflicts, setConflicts] = useState<KnowledgeConflict[]>([]);
  const [merges, setMerges] = useState<EntityMerge[]>([]);
  const [disabled, setDisabled] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getKnowledgeReview()
      .then((data) => {
        setConflicts(data.conflicts);
        setMerges(data.merges);
        setDisabled(!data.enabled);
        setError(null);
        onCountChange?.(data.conflicts.length);
      })
      .catch(() => setError("Could not load the review queue."))
      .finally(() => setLoaded(true));
  }, [onCountChange]);

  useEffect(() => { refresh(); }, [refresh]);

  useVaultEvents((event) => {
    if (
      event.type === "graphrag.conflicts" ||
      event.type === "graphrag.indexed" ||
      event.type === "graphrag.entities_merged"
    ) {
      refresh();
    }
  });

  return (
    <div className="kv-review-panel">
      <div className="kv-review-panel-head">
        <span className="kv-review-panel-title">Fact review</span>
        <button className="kv-review-close" onClick={onClose} aria-label="Close review panel">
          ×
        </button>
      </div>
      <div className="kv-review-body">
        {!loaded && <div className="kv-loading">Loading…</div>}
        {loaded && error && (
          <div className="kv-review-empty kv-review-empty--error">{error}</div>
        )}
        {loaded && !error && disabled && (
          <div className="kv-review-empty">GraphRAG is not initialized.</div>
        )}
        {loaded && !error && !disabled && conflicts.length === 0 && merges.length === 0 && (
          <div className="kv-review-empty">No conflicts awaiting review.</div>
        )}
        {loaded && conflicts.length > 0 && (
          <>
            <div className="kv-review-section">
              Contradicting facts ({conflicts.length})
            </div>
            {conflicts.map((c) => (
              <ConflictCard key={c.id} conflict={c} onResolved={refresh} />
            ))}
          </>
        )}
        {loaded && merges.length > 0 && (
          <>
            <div className="kv-review-section">Recent merges ({merges.length})</div>
            {merges.map((m) => (
              <MergeRow key={m.id} merge={m} onUndone={refresh} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
