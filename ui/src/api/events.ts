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
 * The connection auto-reconnects on drop. Frames received during disconnect
 * are lost (best-effort bus on the server side).
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

/** Subscribe to vault/index events. Returns an `unsubscribe()` cleanup. */
export function subscribeVaultEvents(listener: VaultEventListener): () => void {
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (closed) return;
    source = new EventSource(`${BASE}/vault/events`);
    source.onmessage = (ev) => {
      if (!ev.data) return;
      try {
        const parsed = JSON.parse(ev.data) as VaultEvent;
        if (parsed && parsed.type && parsed.path !== undefined) {
          listener(parsed);
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    source.onerror = () => {
      source?.close();
      source = null;
      if (closed) return;
      retryTimer = setTimeout(open, 3000);
    };
  };

  open();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    source?.close();
  };
}
