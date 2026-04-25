/**
 * @file Central chat session management hook for Nexus.
 *
 * Owns all chat state (`chatStates` keyed by session key), SSE event routing,
 * abort controller management, lazy history loading, message rollback, and
 * the auto-send logic for hidden seeds.
 *
 * The special key `NEW_KEY` represents the not-yet-created session (empty input
 * before the first message). On receiving `done` from the backend, the session
 * is promoted to its canonical `session_id` via `setActiveSession`.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Message } from "../components/ChatView";
import { chatStream, truncateSession, HIDDEN_SEED_MARKER } from "../api";
import { NEW_KEY, emptyState, type ChatState, type UseChatSessionResult } from "../types/chat";
import { applyDeltaEvent, applyToolEvent, applyDoneEvent, applyLimitReachedEvent, applyErrorEvent } from "./streamEventHandlers";
import { loadSessionHistory as loadHistory } from "./loadSessionHistory";
import { tryRecoverSession, appendConnectionErrorBanner } from "./sendHelpers";

export type { UseChatSessionResult };

/**
 * Hook for managing multiple concurrent chat sessions with SSE streaming.
 *
 * Maintains a `Map<sessionKey, ChatState>` to preserve the state of all open
 * sessions simultaneously — switching sessions or views never interrupts an
 * in-progress stream or loses typed text.
 *
 * @param deps.availableModels - List of models available for selection.
 * @param deps.lastUsedModel - Last model used by the user (persisted).
 * @param deps.defaultModel - Default model from the server configuration.
 * @param deps.persistUsedModel - Persists the chosen model for future sessions.
 * @param freshSessionId - Factory that generates a UUID for new pending sessions;
 *   needed to open the event SSE channel before the first send.
 * @returns Full set of state and handlers for the chat views.
 */
export function useChatSession(
  deps: { availableModels: string[]; lastUsedModel: string; defaultModel: string; persistUsedModel: (model: string) => void },
  freshSessionId: () => string,
): UseChatSessionResult {
  const { availableModels, lastUsedModel, defaultModel, persistUsedModel } = deps;

  const [chatStates, setChatStates] = useState<Map<string, ChatState>>(() => {
    const m = new Map<string, ChatState>();
    m.set(NEW_KEY, emptyState());
    return m;
  });
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [sessionsRevision, setSessionsRevision] = useState(0);
  const [pendingSessionId, setPendingSessionId] = useState<string>(() => freshSessionId());

  // AbortController for the in-flight /chat/stream fetch, per session key.
  // Used by the Stop button to tear down the request client-side; the
  // backend-side cancel is a separate POST to /chat/{sid}/cancel.
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());

  // Auto-send queue: when handleOpenInChat navigates to a fresh session
  // with a hidden-seed, we stash {sid, seed} here and let a useEffect
  // fire the send() call after activeSession has propagated through state.
  const pendingAutoSend = useRef<{ sid: string; seed: string } | null>(null);

  const activeKey = activeSession ?? NEW_KEY;
  const activeState = chatStates.get(activeKey) ?? emptyState();

  const isRealModel = useCallback(
    (s: string) => !!s && s !== "auto" && availableModels.includes(s),
    [availableModels],
  );

  const computeSeedModel = useCallback((): string => {
    if (isRealModel(lastUsedModel)) return lastUsedModel;
    if (isRealModel(defaultModel)) return defaultModel;
    return availableModels[0] ?? "";
  }, [isRealModel, lastUsedModel, defaultModel, availableModels]);

  const patchState = useCallback((key: string, patch: Partial<ChatState>) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? emptyState();
      next.set(key, { ...cur, ...patch });
      return next;
    });
  }, []);

  const loadSessionHistory = useCallback(
    (id: string) => loadHistory(id, setChatStates, computeSeedModel, patchState),
    [computeSeedModel, patchState],
  );

  const handleSessionSelect = useCallback((id: string) => {
    setActiveSession(id);
    if (!chatStates.has(id) || !chatStates.get(id)!.historyLoaded) void loadSessionHistory(id);
  }, [chatStates, loadSessionHistory]);

  const handleNewChat = useCallback(() => {
    setActiveSession(null);
    setChatStates((prev) => { const next = new Map(prev); next.set(NEW_KEY, { ...emptyState(), selectedModel: computeSeedModel() }); return next; });
    setPendingSessionId(freshSessionId());
  }, [computeSeedModel, freshSessionId]);

  const handleInputChange = useCallback((v: string) => patchState(activeKey, { input: v }), [activeKey, patchState]);

  const handleAttachmentsChange = useCallback(
    (files: { name: string; vaultPath: string }[]) => patchState(activeKey, { attachments: files }),
    [activeKey, patchState],
  );

  const handleModelChange = useCallback((model: string) => {
    patchState(activeKey, { selectedModel: model });
    persistUsedModel(model);
  }, [activeKey, patchState, persistUsedModel]);

  const handleRollback = useCallback(async (visibleIdx: number) => {
    const key = activeKey;
    const state = chatStates.get(key) ?? emptyState();
    if (state.thinking) return;
    const visible = state.messages.filter((m) => (m.content ?? "").trim().length > 0 || m.kind === "limit");
    const targetMsg = visible[visibleIdx];
    if (!targetMsg || targetMsg.role !== "user") return;
    const fullIdx = state.messages.indexOf(targetMsg);
    if (fullIdx === -1) return;
    patchState(key, { messages: state.messages.slice(0, fullIdx), input: targetMsg.content });
    if (activeSession) try { await truncateSession(activeSession, fullIdx); } catch { /* best-effort */ }
  }, [activeKey, activeSession, chatStates, patchState]);

  const send = useCallback(async (override?: unknown) => {
    const key = activeKey;
    const state = chatStates.get(key) ?? emptyState();
    // ``override`` can be a plain string OR ``{ text, inPlace }``.
    // ``inPlace`` resumes a partial assistant: no new user bubble,
    // no new placeholder — deltas stream into the existing last assistant.
    let overrideText: string | undefined;
    let inPlace = false;
    if (typeof override === "string") { overrideText = override; }
    else if (override && typeof override === "object") {
      const o = override as { text?: unknown; inPlace?: unknown };
      if (typeof o.text === "string") overrideText = o.text;
      if (typeof o.inPlace === "boolean") inPlace = o.inPlace;
    }
    const rawText = (overrideText ?? state.input).trim();
    const hasAttachments = state.attachments.length > 0;
    if ((!rawText && !hasAttachments) || state.thinking) return;

    let text = rawText;
    if (hasAttachments) {
      const refs = state.attachments.map((a) => `[${a.name}](vault://${a.vaultPath})`).join("\n");
      text = text ? `${text}\n\n${refs}` : refs;
    }
    const isHidden = text.startsWith(HIDDEN_SEED_MARKER);
    const userMsg: Message = { role: "user", content: text, timestamp: new Date(), attachments: hasAttachments ? [...state.attachments] : undefined };
    const placeholderAsst: Message = { role: "assistant", content: "", trace: [], timeline: [], timestamp: new Date(), streaming: true };
    // In-place resume: keep the trailing assistant, clear its partial flag,
    // mark it streaming, let delta/tool events append to it. No user bubble.
    const lastIsAssistant = state.messages.length > 0 && state.messages[state.messages.length - 1].role === "assistant";
    const resumeInPlace = inPlace && lastIsAssistant;
    patchState(key, {
      input: "", thinking: true, attachments: [],
      messages: resumeInPlace
        ? state.messages.map((m, i) => i === state.messages.length - 1 ? { ...m, partial: undefined, streaming: true } : m)
        : isHidden ? [...state.messages, placeholderAsst] : [...state.messages, userMsg, placeholderAsst],
    });

    // For a new chat, send our client-side session id so the HITL
    // EventSource (opened on that id) and the backend's session agree.
    const sidForPost = activeSession ?? pendingSessionId;
    const abortController = new AbortController();
    abortControllersRef.current.set(key, abortController);
    const sendModel = state.selectedModel && state.selectedModel !== "auto" ? state.selectedModel : "";
    let sawDone = false;

    try {
      await chatStream(text, sidForPost, (event) => {
        if (event.type === "delta") {
          applyDeltaEvent(setChatStates, key, event.text);
        } else if (event.type === "tool") {
          applyToolEvent(setChatStates, key, { name: event.name, args: event.args, result_preview: event.result_preview });
        } else if (event.type === "done") {
          sawDone = true;
          applyDoneEvent(setChatStates, (id) => setActiveSession(id), setSessionsRevision, persistUsedModel, key, activeSession, state.selectedModel, event);
        } else if (event.type === "limit_reached") {
          applyLimitReachedEvent(setChatStates, key, event.iterations);
        } else if (event.type === "error") {
          applyErrorEvent(setChatStates, key, event.reason, event.detail);
        }
      }, abortController.signal, sendModel);

      if (!sawDone && !abortController.signal.aborted) {
        // Server closed the stream without a terminal `done`. Pull persisted
        // state so any partial progress surfaces in the UI.
        const recoverSid = activeSession ?? sidForPost;
        if (recoverSid) {
          setChatStates((prev) => { const next = new Map(prev); const cur = next.get(key) ?? emptyState(); next.set(key, { ...cur, historyLoaded: false, thinking: false }); return next; });
          if (!activeSession) setActiveSession(recoverSid);
          void loadSessionHistory(recoverSid);
        } else {
          patchState(key, { thinking: false });
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User clicked Stop; the UI was already updated by handleStop.
      } else {
        // Network/fetch error — try to recover persisted partial progress.
        const recoverSid = activeSession ?? sidForPost;
        const recovered = recoverSid
          ? await tryRecoverSession(recoverSid, key, activeSession, setChatStates, setActiveSession, loadSessionHistory)
          : false;
        if (!recovered) appendConnectionErrorBanner(err, key, setChatStates);
      }
    } finally {
      if (abortControllersRef.current.get(key) === abortController) abortControllersRef.current.delete(key);
    }
  }, [activeKey, activeSession, chatStates, patchState, pendingSessionId, loadSessionHistory, persistUsedModel]);

  // Fire off the queued auto-send once activeSession has propagated. This
  // is how the Kanban "Open in chat" icon kicks off a hidden-seed turn
  // immediately, without the user seeing the seed in the input.
  useEffect(() => {
    const pending = pendingAutoSend.current;
    if (!pending || pending.sid !== activeSession) return;
    pendingAutoSend.current = null;
    void send(pending.seed);
  }, [activeSession, send]);

  const handleStop = useCallback(() => {
    const key = activeKey;
    const sidForCancel = activeSession ?? pendingSessionId;
    // Best-effort server cancel (unblocks HITL waits + cancels the turn task).
    fetch(`${import.meta.env.VITE_NEXUS_API ?? "http://localhost:18989"}/chat/${encodeURIComponent(sidForCancel)}/cancel`, { method: "POST" }).catch(() => {});
    abortControllersRef.current.get(key)?.abort();
    // Flip thinking off and mark the placeholder as stopped.
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key);
      if (!cur) return prev;
      const msgs = cur.messages.slice();
      const lastIdx = msgs.length - 1;
      if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
        const existing = msgs[lastIdx].content;
        msgs[lastIdx] = { ...msgs[lastIdx], content: existing ? `${existing}\n\n_[stopped by user]_` : "_[stopped by user]_", streaming: false };
      }
      next.set(key, { ...cur, messages: msgs, thinking: false });
      return next;
    });
  }, [activeKey, activeSession, pendingSessionId]);

  const dismissLimitBanner = useCallback(() => {
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(activeKey);
      if (!cur) return prev;
      next.set(activeKey, { ...cur, messages: cur.messages.filter((m) => m.kind !== "limit") });
      return next;
    });
  }, [activeKey]);

  const handleContinue = useCallback(() => {
    dismissLimitBanner();
    // Same hidden-seed / in-place trick the partial-turn Continue uses:
    // the user clicked a button, don't add a "continue" user bubble.
    void send({ text: `${HIDDEN_SEED_MARKER}continue`, inPlace: true });
  }, [dismissLimitBanner, send]);

  const handleContinuePartial = useCallback((_visibleIdx: number) => {
    // Continue **in place** — no "continue" user bubble. The existing
    // partial assistant keeps its content and timeline; its ``partial``
    // flag is cleared and ``streaming`` set to true inside ``send`` so
    // delta/tool events append to the same bubble.
    void send({ text: `${HIDDEN_SEED_MARKER}continue`, inPlace: true });
  }, [send]);

  const handleRetryPartial = useCallback(async (visibleIdx: number) => {
    const state = chatStates.get(activeKey) ?? emptyState();
    if (state.thinking) return;
    const visible = state.messages.filter(
      (m) => (m.content ?? "").trim().length > 0 || m.kind === "limit" || (m.timeline ?? []).length > 0 || m.partial != null,
    );
    // Walk back from the clicked assistant to find its preceding user message.
    let userVisibleIdx = -1;
    for (let i = visibleIdx - 1; i >= 0; i--) {
      if (visible[i].role === "user") { userVisibleIdx = i; break; }
    }
    if (userVisibleIdx === -1) return;
    const targetUser = visible[userVisibleIdx];
    const targetAsst = visible[visibleIdx];
    // Drop the partial assistant from the UI so the retry's placeholder replaces it.
    const fullAsstIdx = state.messages.indexOf(targetAsst);
    if (fullAsstIdx === -1) return;
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(activeKey) ?? emptyState();
      next.set(activeKey, { ...cur, messages: cur.messages.slice(0, fullAsstIdx) });
      return next;
    });
    // Fire the retry with a hidden seed: no duplicate user bubble.
    void send(`${HIDDEN_SEED_MARKER}retry: ${targetUser.content}`);
  }, [activeKey, chatStates, send]);

  return {
    chatStates, setChatStates, activeKey, activeState, activeSession, setActiveSession,
    pendingSessionId, setPendingSessionId, sessionsRevision, setSessionsRevision,
    pendingAutoSend, send, handleStop, handleRollback, handleContinue,
    handleContinuePartial, handleRetryPartial, handleInputChange,
    handleAttachmentsChange, handleModelChange, handleSessionSelect,
    handleNewChat, loadSessionHistory, patchState, computeSeedModel, dismissLimitBanner,
  };
}
