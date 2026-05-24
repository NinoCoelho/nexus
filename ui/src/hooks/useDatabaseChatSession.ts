/**
 * useDatabaseChatSession — minimal one-session chat hook for the floating
 * data-chat bubble. Persists the session id in `_data.md` so it survives
 * page reloads, and bootstraps via /vault/dispatch with mode "chat-hidden"
 * the first time the user sends a message in a fresh database.
 *
 * Intentionally NOT reusing the heavy `useChatSession` hook: the bubble
 * only ever has ONE session at a time, doesn't need the multi-key map,
 * and shouldn't compete with the main ChatView's session state.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { chatStream, type StreamEvent } from "../api/chat";
import { dispatchFromVault } from "../api/dispatch";
import { fetchDashboard, putDashboard } from "../api/dashboard";
import { getSession } from "../api/sessions";

export interface BubbleMessage {
  role: "user" | "assistant";
  content: string;
  /** Hidden seed messages (start with `<!-- nx:hidden-seed -->`) are filtered. */
  hidden?: boolean;
  pending?: boolean;
}

export interface DatabaseChatSession {
  sessionId: string | null;
  messages: BubbleMessage[];
  thinking: boolean;
  send: (text: string) => Promise<void>;
  /** Run a chat-kind operation: same as `send(prompt)` but exposed for clarity. */
  runOperation: (prompt: string) => Promise<void>;
  /** Clear messages + create a new session on next send. Persists null sid. */
  startFresh: () => Promise<void>;
  error: string | null;
  /** True iff a bootstrap dispatch is in flight (first message). */
  bootstrapping: boolean;
}

const HIDDEN_SEED_PREFIX = "<!-- nx:hidden-seed -->";

export interface UseDatabaseChatSessionOptions {
  /** Fires after every assistant turn finishes (success or error). Used by
   *  the dashboard view to reload `_data.md` so agent-driven mutations
   *  (e.g. `dashboard_manage.add_operation`) become visible without a manual
   *  page refresh. */
  onTurnComplete?: () => void;
}

export function useDatabaseChatSession(
  folder: string | null,
  options?: UseDatabaseChatSessionOptions,
): DatabaseChatSession {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<BubbleMessage[]>([]);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bootstrapping, setBootstrapping] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const folderRef = useRef(folder);
  folderRef.current = folder;
  const onTurnCompleteRef = useRef(options?.onTurnComplete);
  onTurnCompleteRef.current = options?.onTurnComplete;

  // Load existing session id (and history) when folder changes.
  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setSessionId(null);
    setError(null);
    setThinking(false);
    if (!folder) return;
    (async () => {
      try {
        const dash = await fetchDashboard(folder);
        if (cancelled || folderRef.current !== folder) return;
        if (dash.chat_session_id) {
          setSessionId(dash.chat_session_id);
          // Load existing message history.
          try {
            const detail = await getSession(dash.chat_session_id);
            if (cancelled || folderRef.current !== folder) return;
            const visible: BubbleMessage[] = detail.messages
              .filter((m) => m.role === "user" || m.role === "assistant")
              .map((m) => ({
                role: m.role as "user" | "assistant",
                content: m.content,
                hidden: m.content.startsWith(HIDDEN_SEED_PREFIX),
              }));
            setMessages(visible);
          } catch {
            // History fetch is best-effort; the session id is still valid.
          }
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [folder]);

  /** Run a chat turn against `sid` (creating one via dispatch if null). */
  const sendInternal = useCallback(async (
    sid: string | null,
    text: string,
    folder_: string,
  ): Promise<string | null> => {
    const ac = new AbortController();
    abortRef.current = ac;
    setThinking(true);
    setError(null);
    let activeSid = sid;
    try {
      // Bootstrap: no session yet → call dispatch to seed database context.
      if (!activeSid) {
        setBootstrapping(true);
        try {
          const dispatch = await dispatchFromVault({
            mode: "chat-hidden",
            // Use folder-only dispatch path on the server.
            // Cast through unknown because the existing DispatchBody type
            // requires `path`, but the server now accepts folder-only too.
            ...({ folder: folder_ } as unknown as { path: string }),
          });
          activeSid = dispatch.session_id;
          if (folderRef.current === folder_) {
            setSessionId(activeSid);
            // Persist the new session id in _data.md.
            void putDashboard(folder_, { chat_session_id: activeSid }).catch(() => {});
            // Echo the hidden seed into messages for symmetry with reload-loaded
            // history (it'll be filtered out of the visible list).
            if (dispatch.seed_message) {
              setMessages((prev) => [
                ...prev,
                {
                  role: "user",
                  content: dispatch.seed_message!,
                  hidden: true,
                },
              ]);
            }
            // Fire the seed message into the chat stream silently — this is
            // what gives the agent its first context turn.
            if (dispatch.seed_message) {
              await new Promise<void>((resolve, reject) => {
                let buf = "";
                chatStream(
                  dispatch.seed_message!,
                  activeSid!,
                  (event: StreamEvent) => {
                    if (event.type === "delta") buf += event.text;
                    if (event.type === "done") resolve();
                    if (event.type === "error") reject(new Error(event.detail));
                  },
                  ac.signal,
                ).catch(reject);
                // The first response to the seed is contextual setup the user
                // doesn't need to see — drop it.
                void buf;
              });
            }
          }
        } finally {
          setBootstrapping(false);
        }
      }

      // Append the user message + a placeholder assistant message that fills as
      // deltas stream in.
      setMessages((prev) => [
        ...prev,
        { role: "user", content: text },
        { role: "assistant", content: "", pending: true },
      ]);

      let acc = "";
      await chatStream(
        text,
        activeSid!,
        (event: StreamEvent) => {
          if (event.type === "delta") {
            acc += event.text;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant" && last.pending) {
                next[next.length - 1] = { ...last, content: acc };
              }
              return next;
            });
          } else if (event.type === "done") {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant" && last.pending) {
                next[next.length - 1] = { role: "assistant", content: event.reply || acc };
              }
              return next;
            });
          } else if (event.type === "error") {
            setError(event.detail);
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant" && last.pending) {
                next[next.length - 1] = {
                  role: "assistant",
                  content: `(error: ${event.detail})`,
                };
              }
              return next;
            });
          }
        },
        ac.signal,
      );
      return activeSid;
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError((e as Error).message);
      }
      return activeSid;
    } finally {
      setThinking(false);
      abortRef.current = null;
      try {
        onTurnCompleteRef.current?.();
      } catch {
        /* swallow — listener errors must not break the chat flow */
      }
    }
  }, []);

  const send = useCallback(async (text: string) => {
    if (!folder || !text.trim()) return;
    await sendInternal(sessionId, text.trim(), folder);
  }, [folder, sessionId, sendInternal]);

  const runOperation = useCallback(async (prompt: string) => {
    await send(prompt);
  }, [send]);

  const startFresh = useCallback(async () => {
    if (!folder) return;
    abortRef.current?.abort();
    setMessages([]);
    setSessionId(null);
    setError(null);
    try {
      await putDashboard(folder, { chat_session_id: null });
    } catch (e) {
      setError((e as Error).message);
    }
  }, [folder]);

  return { sessionId, messages, thinking, send, runOperation, startFresh, error, bootstrapping };
}
