/**
 * Vault event stream client.
 *
 * Subscribes to the server-sent event stream at `/vault/events`, which
 * publishes notifications when background indexing completes for a vault
 * file. Events:
 *   - `vault.indexed`     — FTS / metadata index updated for a path
 *   - `vault.removed`     — a path was deleted/moved
 *   - `graphrag.indexed`  — GraphRAG finished indexing a path
 *   - `graphrag.removed`  — GraphRAG dropped chunks for a path
 *
 * All callers share a single EventSource — opening one per component
 * burns through Chrome's 6-connection per-host HTTP/1.1 limit and starves
 * fetches (e.g. /vault/kanban) when many vault-aware components are
 * mounted at once. The connection is opened on the first subscriber and
 * closed when the last unsubscribes; auto-reconnects on drop.
 */
import { BASE } from "./base";

export type VaultEventType =
  | "vault.indexed"
  | "vault.removed"
  | "graphrag.indexed"
  | "graphrag.removed";

export interface VaultEvent {
  type: VaultEventType;
  path: string;
}

export type VaultEventListener = (event: VaultEvent) => void;

let source: EventSource | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<VaultEventListener>();

function openShared(): void {
  if (source) return;
  const es = new EventSource(`${BASE}/vault/events`);
  es.onmessage = (ev) => {
    if (!ev.data) return;
    let parsed: VaultEvent;
    try {
      parsed = JSON.parse(ev.data) as VaultEvent;
    } catch {
      return;
    }
    if (!parsed || !parsed.type || parsed.path === undefined) return;
    for (const fn of listeners) {
      try { fn(parsed); } catch { /* one bad listener shouldn't kill the rest */ }
    }
  };
  es.onerror = () => {
    es.close();
    source = null;
    if (listeners.size === 0) return;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (listeners.size > 0) openShared();
    }, 3000);
  };
  source = es;
}

/** Subscribe to vault/index events. Returns an `unsubscribe()` cleanup. */
export function subscribeVaultEvents(listener: VaultEventListener): () => void {
  listeners.add(listener);
  openShared();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
      source?.close();
      source = null;
    }
  };
}
