/**
 * Helpers for the send() function in useChatSession — extracted to keep
 * useChatSession.ts under the 300 LOC limit.
 */
import type { Message } from "../components/ChatView";
import { getSession } from "../api";
import { emptyState, type ChatState } from "../types/chat";

type SetChatStates = React.Dispatch<React.SetStateAction<Map<string, ChatState>>>;

/**
 * After a stream that closed without a `done` event, or after a network error,
 * try to recover partial progress from the backend's persisted session.
 * Returns true if recovery succeeded (history reload was triggered).
 */
export async function tryRecoverSession(
  recoverSid: string,
  key: string,
  activeSession: string | null,
  setChatStates: SetChatStates,
  setActiveSession: (id: string | null) => void,
  loadSessionHistory: (id: string) => Promise<void>,
): Promise<boolean> {
  try {
    const detail = await getSession(recoverSid);
    if (detail.messages.length > 0) {
      setChatStates((prev) => {
        const next = new Map(prev);
        const cur = next.get(key) ?? emptyState();
        next.set(key, { ...cur, historyLoaded: false, thinking: false });
        return next;
      });
      if (!activeSession) setActiveSession(recoverSid);
      void loadSessionHistory(recoverSid);
      return true;
    }
  } catch { /* fall through */ }
  return false;
}

/**
 * Append a connection-lost error banner to the chat when we cannot
 * recover partial progress from the server.
 */
export function appendConnectionErrorBanner(
  err: unknown,
  key: string,
  setChatStates: SetChatStates,
) {
  const errMsg: Message = {
    role: "assistant",
    content: `Connection lost: ${err instanceof Error ? err.message : "request failed"}. The server may still be processing — refresh to see any saved progress.`,
    timestamp: new Date(),
  };
  setChatStates((prev) => {
    const next = new Map(prev);
    const cur = next.get(key) ?? emptyState();
    const msgs = cur.messages.slice(0, -1).concat(errMsg);
    next.set(key, { ...cur, messages: msgs, thinking: false });
    return next;
  });
}
