/**
 * Inline banner: "X files changed since last index — Reindex / Dismiss".
 * Not a modal — never blocks the underlying graph.
 */

import type { FolderStaleResult } from "../../api/folderGraph";

interface Props {
  stale: FolderStaleResult;
  onReindex: () => void;
  onDismiss: () => void;
}

export function StalePrompt({ stale, onReindex, onDismiss }: Props) {
  const total = stale.added.length + stale.changed.length + stale.removed.length;
  if (total === 0) return null;

  const parts: string[] = [];
  if (stale.added.length > 0) parts.push(`${stale.added.length} added`);
  if (stale.changed.length > 0) parts.push(`${stale.changed.length} changed`);
  if (stale.removed.length > 0) parts.push(`${stale.removed.length} removed`);

  return (
    <div className="fg-stale-banner">
      <span className="fg-stale-text">
        {total} file{total === 1 ? "" : "s"} {parts.join(", ")} since last index.
      </span>
      <div className="fg-stale-actions">
        <button type="button" className="fg-btn fg-btn--small fg-btn--primary" onClick={onReindex}>
          Reindex
        </button>
        <button type="button" className="fg-btn fg-btn--small fg-btn--ghost" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
