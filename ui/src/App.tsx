import { useCallback, useEffect, useRef, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import ChatView, { type Message } from "./components/ChatView";
import VaultView from "./components/VaultView";
import InsightsView from "./components/InsightsView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import ApprovalDialog from "./components/ApprovalDialog";
import UnifiedGraphView from "./components/UnifiedGraphView";
import {
  chatStream,
  getGraphragIndexStatus,
  getHitlSettings,
  getRouting,
  getSession,
  graphragIndexFile,
  pingHealth,
  putRouting,
  respondToUserRequest,
  fetchPendingRequest,
  subscribeSessionEvents,
  truncateSession,
  HIDDEN_SEED_MARKER,
  type HitlSettings,
  type TraceEvent,
  type UserRequestPayload,
} from "./api";
import { useToast } from "./toast/ToastProvider";

type View = "chat" | "vault" | "graph" | "insights";

/**
 * One entry per session the user has interacted with this tab. Keyed by
 * session id. "__new__" holds state for the not-yet-created session (first
 * message of a fresh chat). Lifted up here so nothing — view switches,
 * session switches, remounts — can drop a pending "thinking" indicator or
 * a half-typed message.
 */
interface ChatState {
  messages: Message[];
  thinking: boolean;
  input: string;
  historyLoaded: boolean;
  attachments: { name: string; vaultPath: string }[];
  selectedModel?: string;
}

const NEW_KEY = "__new__";
const emptyState = (): ChatState => ({
  messages: [],
  thinking: false,
  input: "",
  historyLoaded: true,
  attachments: [],
});

function parseHistoryTimestamp(raw: unknown): Date {
  if (raw == null) return new Date();
  if (typeof raw === "number") return new Date(raw * 1000);
  const parsed = new Date(raw as string);
  return isNaN(parsed.getTime()) ? new Date() : parsed;
}

/**
 * Turn a raw upstream transport error into a human-friendly message.
 *
 * Input shape (after backend hardening, see llm.py):
 *   HTTP 500: {"error":{"code":"1234","message":"Network error, error id: ..., please try again later"}}
 *
 * We try to extract the nested provider message; if that fails we fall back
 * to the raw detail. Kept intentionally forgiving — any unexpected shape
 * still produces a readable line.
 */
function prettifyStreamError(detail: string): string {
  if (!detail) return "Something went wrong.";
  // Strip stray `b'...'` repr wrappers from older backends.
  const stripped = detail.replace(/b'([\s\S]*?)'$/, "$1");
  const httpMatch = stripped.match(/^HTTP\s+(\d+):\s*(.+)$/s);
  if (httpMatch) {
    const status = httpMatch[1];
    const body = httpMatch[2].trim();
    try {
      const parsed = JSON.parse(body);
      const msg =
        parsed?.error?.message ??
        parsed?.message ??
        parsed?.detail;
      if (typeof msg === "string" && msg.length > 0) {
        return `Upstream provider error (HTTP ${status}): ${msg}`;
      }
    } catch {
      // body wasn't JSON — fall through
    }
    return `Upstream provider error (HTTP ${status}). ${body.slice(0, 180)}`;
  }
  return detail;
}

function freshSessionId(): string {
  const raw =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return raw.replace(/-/g, "");
}

/** Parse ?view=vault&path=... deep link on first mount. */
function readInitialView(): { view: View; vaultPath: string | null } {
  if (typeof window === "undefined") return { view: "chat", vaultPath: null };
  const qs = new URLSearchParams(window.location.search);
  const v = qs.get("view");
  const path = qs.get("path");
  const allowed: View[] = ["chat", "vault", "graph", "insights"];
  const view = (allowed as string[]).includes(v ?? "") ? (v as View) : "chat";
  return { view, vaultPath: path };
}

export default function App() {
  const toast = useToast();
  const initial = readInitialView();
  const [view, setView] = useState<View>(initial.view);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [sessionsRevision, setSessionsRevision] = useState(0);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);
  const [hasModel, setHasModel] = useState<boolean | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [lastUsedModel, setLastUsedModel] = useState<string>("");
  const [defaultModel, setDefaultModel] = useState<string>("");
  /** Bumps a vault path into VaultView when user clicks "Open in Vault" from a preview modal. */
  const [vaultOpenPath, setVaultOpenPath] = useState<string | null>(initial.vaultPath);
  /** The currently selected file path in the vault tree (lifted so Sidebar tree + editor share it). */
  const [vaultSelectedPath, setVaultSelectedPath] = useState<string | null>(initial.vaultPath);
  // Client-provisioned session id for the "new chat" slot — lets the
  // HITL EventSource open before the first POST. Regenerated on
  // handleNewChat so a reset gives a clean stream.
  const [pendingSessionId, setPendingSessionId] = useState<string>(() => freshSessionId());
  // AbortController for the in-flight /chat/stream fetch, per session key.
  // Used by the Stop button to tear down the request client-side; the
  // backend-side cancel is a separate POST to /chat/{sid}/cancel.
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());
  // Active HITL request (if any). Rendered as the ApprovalDialog.
  const [pendingRequest, setPendingRequest] = useState<UserRequestPayload | null>(null);
  const [pendingRequestSession, setPendingRequestSession] = useState<string | null>(null);
  // YOLO flag, surfaced as a header badge. Fetched from the server;
  // refreshed whenever settings are edited.
  const [yoloMode, setYoloMode] = useState<boolean>(false);
  const [graphSourceFilter, setGraphSourceFilter] = useState<{ mode: "file" | "folder"; path: string } | null>(null);
  const [pendingGraphIndex, setPendingGraphIndex] = useState<string | null>(null);
  // Backend-reachability pill. Polls /health every 15s; shows when the
  // server is unreachable so the user can tell "server is down" apart
  // from "model is still thinking". Starts as null (unknown) — never
  // shows the banner on first load before the first ping resolves.
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      void pingHealth().then((ok) => {
        if (!cancelled) setBackendUp(ok);
      });
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const handleOpenInVault = useCallback((path: string) => {
    setVaultOpenPath(path);
    setVaultSelectedPath(path);
    setView("vault");
  }, []);

  const handleViewEntityGraph = useCallback((mode: "file" | "folder", path: string) => {
    setGraphSourceFilter({ mode, path });
    setView("graph");
  }, []);

  const handleDispatchToChat = useCallback((sessionId: string, seedMessage: string) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(sessionId);
      next.set(sessionId, {
        messages: cur?.messages ?? [],
        thinking: false,
        input: seedMessage,
        historyLoaded: cur?.historyLoaded ?? false,
        attachments: [],
      });
      return next;
    });
    setActiveSession(sessionId);
    setView("chat");
    setSessionsRevision((r) => r + 1);
  }, []);

  // Auto-send queue: when handleOpenInChat navigates to a fresh session
  // with a hidden-seed, we stash {sid, seed} here and let a useEffect
  // fire the send() call after activeSession has propagated through state.
  const pendingAutoSend = useRef<{ sid: string; seed: string } | null>(null);

  const handleOpenInChat = useCallback((sessionId: string, seedMessage: string, title: string) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      next.set(sessionId, {
        messages: [],
        thinking: false,
        input: "",
        historyLoaded: true, // skip GET /sessions — the only "message" is the hidden seed
        attachments: [],
      });
      return next;
    });
    pendingAutoSend.current = { sid: sessionId, seed: seedMessage };
    setActiveSession(sessionId);
    setView("chat");
    setSessionsRevision((r) => r + 1);
    void title; // title was set server-side on dispatch
  }, []);

  const [chatStates, setChatStates] = useState<Map<string, ChatState>>(() => {
    const m = new Map<string, ChatState>();
    m.set(NEW_KEY, emptyState());
    return m;
  });

  const activeKey = activeSession ?? NEW_KEY;
  const activeState = chatStates.get(activeKey) ?? emptyState();

  // Refresh routing/model availability when settings change.
  useEffect(() => {
    let cancelled = false;
    getRouting()
      .then((r) => {
        if (!cancelled) {
          setHasModel((r.available_models?.length ?? 0) > 0);
          setAvailableModels(r.available_models ?? []);
          const lum = r.last_used_model ?? "";
          const def = r.default_model ?? "";
          setLastUsedModel(lum);
          setDefaultModel(def);
          const isReal = (s: string) => s && s !== "auto" && (r.available_models ?? []).includes(s);
          setChatStates((prev) => {
            const next = new Map(prev);
            const cur = next.get(NEW_KEY);
            if (cur && !cur.selectedModel) {
              const seed = isReal(lum) ? lum : (isReal(def) ? def : (r.available_models?.[0] ?? ""));
              next.set(NEW_KEY, { ...cur, selectedModel: seed });
            }
            return next;
          });
        }
      })
      .catch(() => {
        if (!cancelled) setHasModel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

  // Pull YOLO flag from the server. Refreshed on every settings
  // revision so toggling it inside the drawer updates the badge.
  useEffect(() => {
    let cancelled = false;
    getHitlSettings()
      .then((s: HitlSettings) => {
        if (!cancelled) setYoloMode(s.yolo_mode);
      })
      .catch(() => {
        // Backend doesn't speak /settings (older binary, or offline)
        // — hide the badge rather than crashing the layout.
        if (!cancelled) setYoloMode(false);
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

  // Poll for GraphRAG single-file indexing status. Fires when a file is
  // submitted for indexing via KnowledgeView; survives navigation because
  // the effect and state live here in App.
  useEffect(() => {
    if (!pendingGraphIndex) return;
    let active = true;
    const capturedPath = pendingGraphIndex;
    const interval = setInterval(() => {
      getGraphragIndexStatus(capturedPath)
        .then((res) => {
          if (!active) return;
          if (res.status === "done") {
            const n = res.node_count ?? res.nodes?.length ?? 0;
            const name = capturedPath.split("/").pop() ?? capturedPath;
            setPendingGraphIndex(null);
            toast.success(`Indexing complete — ${n} entit${n === 1 ? "y" : "ies"} found for ${name}`, {
              duration: 8000,
              action: {
                label: "View graph",
                onClick: () => handleViewEntityGraph("file", capturedPath),
              },
            });
          } else if (res.status === "error") {
            setPendingGraphIndex(null);
            toast.error("Indexing failed", { detail: res.detail });
          }
        })
        .catch(() => {});
    }, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [pendingGraphIndex]);

  const handleStartGraphIndex = useCallback(async (path: string) => {
    try {
      const res = await graphragIndexFile(path);
      if (res.enabled === false) {
        toast.error("GraphRAG not configured — add an API key in settings");
        return;
      }
      if (res.reason) {
        toast.info(res.reason === "empty file" ? "File is empty — nothing to index" : res.reason);
        return;
      }
      if (res.queued) {
        setPendingGraphIndex(path);
        const name = path.split("/").pop() ?? path;
        toast.info(`Indexing started for ${name}…`);
      }
    } catch (e) {
      toast.error("Failed to start indexing", { detail: e instanceof Error ? e.message : undefined });
    }
  }, [toast]);

  // Subscribe to the session's HITL event stream. The UI owns a
  // ``pendingSessionId`` for the not-yet-created "new chat" so the
  // EventSource can open before the first POST — no chicken-and-egg.
  // Once a real ``activeSession`` exists we prefer that.
  const hitlSessionId = activeSession ?? pendingSessionId;
  useEffect(() => {
    if (!hitlSessionId) return;

    // Recover any request that was published before the EventSource
    // (re)opened — the publish bus is fire-and-forget, so reload /
    // late subscribe / tab restore would otherwise miss the modal.
    let cancelled = false;
    fetchPendingRequest(hitlSessionId)
      .then((req) => {
        if (cancelled || !req) return;
        setPendingRequest(req);
        setPendingRequestSession(hitlSessionId);
      })
      .catch(() => {});

    const es = subscribeSessionEvents(hitlSessionId, (event) => {
      if (event.kind === "user_request") {
        setPendingRequest(event.data);
        setPendingRequestSession(hitlSessionId);
        return;
      }
      if (
        event.kind === "user_request_cancelled" ||
        event.kind === "user_request_auto"
      ) {
        setPendingRequest(null);
        setPendingRequestSession(null);
        return;
      }
      // iter / reply / tool_call / tool_result are already handled by
      // the /chat/stream POST response — ignore here to avoid
      // double-counting the activity strip.
    });
    return () => {
      cancelled = true;
      es.close();
    };
  }, [hitlSessionId]);

  const handleApprovalSubmit = useCallback(
    async (answer: string | Record<string, unknown>) => {
      const req = pendingRequest;
      const sid = pendingRequestSession;
      setPendingRequest(null);
      setPendingRequestSession(null);
      if (!req || !sid) return;
      try {
        await respondToUserRequest(sid, req.request_id, answer);
      } catch {
        // Stale responses (404) are fine — the dialog is already
        // closed. Any other error is rare enough to log and ignore.
      }
    },
    [pendingRequest, pendingRequestSession],
  );

  const handleApprovalTimeout = useCallback(() => {
    setPendingRequest(null);
    setPendingRequestSession(null);
  }, []);

  const patchState = useCallback((key: string, patch: Partial<ChatState>) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? emptyState();
      next.set(key, { ...cur, ...patch });
      return next;
    });
  }, []);

  const appendMessage = useCallback((key: string, msg: Message) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? emptyState();
      next.set(key, { ...cur, messages: [...cur.messages, msg] });
      return next;
    });
  }, []);

  const loadSessionHistory = useCallback(async (id: string) => {
    try {
      const detail = await getSession(id);
      // Hydrate badges from persisted tool_calls + tool result messages.
      // Assistant messages with tool_calls are followed by role="tool"
      // entries carrying the result; pair them so the UI can render the
      // same timeline it showed live.
      const raw = detail.messages;
      const msgs: Message[] = [];
      for (let i = 0; i < raw.length; i++) {
        const m = raw[i];
        if (m.role === "user") {
          const content = m.content ?? "";
          if (content.trim().length === 0) continue;
          // Hidden seeds are persisted so the agent has context on reload,
          // but they shouldn't show up as chat bubbles.
          if (content.startsWith(HIDDEN_SEED_MARKER)) continue;
          msgs.push({
            role: "user",
            content,
            timestamp: parseHistoryTimestamp(m.created_at),
          });
          continue;
        }
        if (m.role !== "assistant") continue;
        const toolCalls = Array.isArray(m.tool_calls)
          ? (m.tool_calls as Array<{ id?: string; name?: string; arguments?: unknown }>)
          : [];
        // Assistants whose content was stamped with a partial-status prefix
        // ([interrupted], [cancelled], [iteration_limit], [empty_response],
        // [llm_error], [crashed]) had their turn aborted mid-flight — their
        // tool calls without a paired result are genuinely unfinished.
        // Everything else is a completed turn: default tools to "done" so
        // the detail modal doesn't show a forever-running indicator.
        const rawContent = m.content ?? "";
        const partialMatch = rawContent.match(/^\[(interrupted|cancelled|iteration_limit|empty_response|llm_error|crashed)\]\s*/);
        const isPartial = partialMatch != null;
        const partialStatus = (partialMatch?.[1] ?? "interrupted") as NonNullable<Message["partial"]>["status"];
        const content = isPartial ? rawContent.slice(partialMatch![0].length) : rawContent;
        // Collect paired tool results that follow this assistant message,
        // keyed by tool_call_id if available, otherwise by position.
        const resultsById = new Map<string, string>();
        const resultsByPos: string[] = [];
        let j = i + 1;
        while (j < raw.length && raw[j].role === "tool") {
          const preview = (raw[j].content ?? "").slice(0, 200);
          const tid = raw[j].tool_call_id ?? "";
          if (tid) resultsById.set(tid, preview);
          resultsByPos.push(preview);
          j++;
        }
        const timeline: NonNullable<Message["timeline"]> = [];
        const trace: TraceEvent[] = [];
        if (content.length > 0) {
          timeline.push({ id: `h-t-${msgs.length}`, type: "text", text: content });
        }
        toolCalls.forEach((tc, tcIdx) => {
          const name = tc.name ?? "";
          if (!name) return;
          const args = tc.arguments;
          const preview = resultsById.get(tc.id ?? "") ?? resultsByPos[tcIdx];
          const status: "pending" | "done" = preview != null
            ? "done"
            : isPartial
              ? "pending"
              : "done";
          timeline.push({
            id: `h-c-${msgs.length}-${timeline.length}`,
            type: "tool",
            tool: name,
            args,
            result_preview: preview,
            status,
          });
          trace.push({ iter: 0, tool: name, args, result: preview } as TraceEvent);
        });
        // Skip fully-empty assistant messages (no text + no tool calls).
        if ((m.content ?? "").trim().length === 0 && timeline.length === 0) {
          continue;
        }
        msgs.push({
          role: "assistant",
          content,
          timestamp: parseHistoryTimestamp(m.created_at),
          timeline: timeline.length > 0 ? timeline : undefined,
          trace: trace.length > 0 ? trace : undefined,
          ...(isPartial ? { partial: { status: partialStatus } } : {}),
        });
        i = j - 1;
      }
      setChatStates((prev) => {
        const next = new Map(prev);
        const cur = next.get(id);
        // Don't clobber in-flight state: if thinking or local-only messages
        // exist for this session already, preserve them; only seed history
        // for sessions we haven't loaded yet.
        if (cur && cur.historyLoaded) return prev;
        const isReal = (s: string) => !!s && s !== "auto" && availableModels.includes(s);
        const seedModel = cur?.selectedModel
          || (isReal(lastUsedModel) ? lastUsedModel : (isReal(defaultModel) ? defaultModel : (availableModels[0] ?? "")));
        next.set(id, {
          messages: msgs,
          thinking: cur?.thinking ?? false,
          input: cur?.input ?? "",
          historyLoaded: true,
          attachments: cur?.attachments ?? [],
          selectedModel: seedModel,
        });
        return next;
      });
    } catch {
      patchState(id, { historyLoaded: true });
    }
  }, [patchState, availableModels, lastUsedModel, defaultModel]);

  const handleSessionSelect = useCallback((id: string) => {
    setActiveSession(id);
    setView("chat");
    if (!chatStates.has(id) || !chatStates.get(id)!.historyLoaded) {
      void loadSessionHistory(id);
    }
  }, [chatStates, loadSessionHistory]);

  const handleNewChat = useCallback(() => {
    setActiveSession(null);
    setView("chat");
    setChatStates((prev) => {
      const next = new Map(prev);
      const isReal = (s: string) => s && s !== "auto" && availableModels.includes(s);
      const seed = isReal(lastUsedModel)
        ? lastUsedModel
        : (isReal(defaultModel) ? defaultModel : (availableModels[0] ?? ""));
      next.set(NEW_KEY, {
        ...emptyState(),
        selectedModel: seed,
      });
      return next;
    });
    setPendingSessionId(freshSessionId());
    setPendingRequest(null);
    setPendingRequestSession(null);
  }, [lastUsedModel, defaultModel, availableModels]);

  const handleInputChange = useCallback((v: string) => {
    patchState(activeKey, { input: v });
  }, [activeKey, patchState]);

  const handleAttachmentsChange = useCallback(
    (files: { name: string; vaultPath: string }[]) => {
      patchState(activeKey, { attachments: files });
    },
    [activeKey, patchState],
  );

  const handleModelChange = useCallback((model: string) => {
    patchState(activeKey, { selectedModel: model });
    if (model && model !== "auto") {
      setLastUsedModel(model);
      putRouting({ last_used_model: model }).catch(() => {});
    }
  }, [activeKey, patchState]);

  const handleRollback = useCallback(async (visibleIdx: number) => {
    const key = activeKey;
    const state = chatStates.get(key) ?? emptyState();
    if (state.thinking) return;

    const visible = state.messages.filter(
      (m) => (m.content ?? "").trim().length > 0 || m.kind === "limit",
    );
    const targetMsg = visible[visibleIdx];
    if (!targetMsg || targetMsg.role !== "user") return;

    const fullIdx = state.messages.indexOf(targetMsg);
    if (fullIdx === -1) return;

    const rollbackText = targetMsg.content;
    const kept = state.messages.slice(0, fullIdx);

    patchState(key, {
      messages: kept,
      input: rollbackText,
    });

    if (activeSession) {
      try {
        await truncateSession(activeSession, fullIdx);
      } catch { /* best-effort */ }
    }
  }, [activeKey, activeSession, chatStates, patchState]);

  const send = useCallback(async (override?: unknown) => {
    const key = activeKey;
    const state = chatStates.get(key) ?? emptyState();
    // ``override`` can be a plain string (legacy callers, the Send button),
    // OR an options object ``{ text, inPlace }``. ``inPlace`` resumes a
    // partial assistant: no new user bubble, no new placeholder — deltas
    // stream into the existing last assistant. Used by the Continue button
    // on an interrupted / truncated turn.
    let overrideText: string | undefined;
    let inPlace = false;
    if (typeof override === "string") {
      overrideText = override;
    } else if (override && typeof override === "object") {
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
    // In-place resume: keep the trailing assistant message (the partial one
    // the user clicked Continue on), clear its partial flag, mark it
    // streaming, and let the delta/tool event handlers below append to it.
    // No user bubble, no new placeholder.
    const lastIsAssistant =
      state.messages.length > 0 &&
      state.messages[state.messages.length - 1].role === "assistant";
    const resumeInPlace = inPlace && lastIsAssistant;
    patchState(key, {
      input: "",
      thinking: true,
      attachments: [],
      messages: resumeInPlace
        ? state.messages.map((m, i) =>
            i === state.messages.length - 1
              ? { ...m, partial: undefined, streaming: true }
              : m,
          )
        : isHidden
          ? [...state.messages, placeholderAsst]
          : [...state.messages, userMsg, placeholderAsst],
    });

    // For a new chat, send our client-side session id so the HITL
    // EventSource (opened on that id) and the backend's session
    // agree. For an existing chat, keep the activeSession id.
    const sidForPost = activeSession ?? pendingSessionId;

    const abortController = new AbortController();
    abortControllersRef.current.set(key, abortController);

    const sendModel = state.selectedModel && state.selectedModel !== "auto" ? state.selectedModel : "";
    let sawDone = false;

    try {
      await chatStream(
        text,
        sidForPost,
        (event) => {
          if (event.type === "done") sawDone = true;
          if (event.type === "delta") {
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              const msgs = [...cur.messages];
              const lastIdx = msgs.length - 1;
              if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                const prev = msgs[lastIdx];
                const tl = [...(prev.timeline ?? [])];
                if (tl.length > 0 && tl[tl.length - 1].type === "text") {
                  tl[tl.length - 1] = { ...tl[tl.length - 1], text: (tl[tl.length - 1].text ?? "") + event.text };
                } else {
                  tl.push({ id: `t${tl.length}`, type: "text", text: event.text });
                }
                msgs[lastIdx] = { ...prev, content: prev.content + event.text, timeline: tl };
              }
              next.set(key, { ...cur, messages: msgs });
              return next;
            });
          } else if (event.type === "tool") {
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              const msgs = [...cur.messages];
              const lastIdx = msgs.length - 1;
              if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                const prevMsg = msgs[lastIdx];
                const prevTrace = prevMsg.trace ?? [];
                let newTrace: TraceEvent[];
                if (event.result_preview != null) {
                  const matchIdx = [...prevTrace].reverse().findIndex(
                    (e) => e.tool === event.name && e.result == null
                  );
                  if (matchIdx !== -1) {
                    const realIdx = prevTrace.length - 1 - matchIdx;
                    newTrace = prevTrace.map((e, i) =>
                      i === realIdx ? { ...e, result: event.result_preview } : e
                    );
                  } else {
                    newTrace = [...prevTrace, { iter: 0, tool: event.name, args: event.args, result: event.result_preview } as TraceEvent];
                  }
                } else {
                  newTrace = [...prevTrace, { iter: 0, tool: event.name, args: event.args } as TraceEvent];
                }
                const tl = [...(prevMsg.timeline ?? [])];
                if (event.result_preview != null) {
                  const toolIdx = [...tl].reverse().findIndex(
                    (s) => s.type === "tool" && s.tool === event.name && s.status === "pending"
                  );
                  if (toolIdx !== -1) {
                    const realIdx = tl.length - 1 - toolIdx;
                    tl[realIdx] = { ...tl[realIdx], result: event.result_preview, result_preview: typeof event.result_preview === "string" ? event.result_preview : undefined, status: "done" as const };
                  } else {
                    tl.push({ id: `t${tl.length}`, type: "tool", tool: event.name, args: event.args, result: event.result_preview, result_preview: typeof event.result_preview === "string" ? event.result_preview : undefined, status: "done" });
                  }
                } else {
                  tl.push({ id: `t${tl.length}`, type: "tool", tool: event.name, args: event.args, status: "pending" });
                }
                msgs[lastIdx] = { ...prevMsg, trace: newTrace, timeline: tl };
              }
              next.set(key, { ...cur, messages: msgs });
              return next;
            });
          } else if (event.type === "done") {
            const routedModel = event.model;
            const routedBy = event.routed_by ?? "user";

            // Persist the *resolved* model id — never the "auto" sentinel.
            // Prefer what the server actually used; fall back to the user's
            // pick. Skip when neither is a real id.
            const usedModel = (routedModel && routedModel !== "auto")
              ? routedModel
              : (state.selectedModel && state.selectedModel !== "auto" ? state.selectedModel : "");
            if (usedModel) {
              putRouting({ last_used_model: usedModel }).catch(() => {});
              setLastUsedModel(usedModel);
            }

            if (!activeSession) {
              // First message — migrate __new__ to the real session id.
              setChatStates((prev) => {
                const next = new Map(prev);
                const fresh = next.get(NEW_KEY) ?? emptyState();
                const lastMsg = fresh.messages[fresh.messages.length - 1];
                const preservedTimeline = lastMsg?.timeline?.map((s) =>
                  s.type === "tool" && s.status === "pending" ? { ...s, status: "done" as const } : s
                );
                const finalAsst: Message = {
                  role: "assistant",
                  content: event.reply,
                  trace: event.trace?.length ? event.trace : undefined,
                  timeline: preservedTimeline,
                  timestamp: new Date(),
                  streaming: false,
                  model: routedModel,
                  routedBy: routedBy,
                };
                const msgs = fresh.messages.slice(0, -1).concat(finalAsst);
                next.set(event.session_id, {
                  messages: msgs,
                  thinking: false,
                  input: "",
                  historyLoaded: true,
                  attachments: [],
                  selectedModel: fresh.selectedModel,
                });
                next.set(NEW_KEY, { ...emptyState(), selectedModel: fresh.selectedModel });
                return next;
              });
              setActiveSession(event.session_id);
            } else {
              // Replace last assistant message with authoritative reply.
              setChatStates((prev) => {
                const next = new Map(prev);
                const cur = next.get(key) ?? emptyState();
                const msgs = [...cur.messages];
                const lastIdx = msgs.length - 1;
                if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                  const lastMsg = msgs[lastIdx];
                  const preservedTimeline = lastMsg?.timeline?.map((s) =>
                    s.type === "tool" && s.status === "pending" ? { ...s, status: "done" as const } : s
                  );
                  msgs[lastIdx] = {
                    role: "assistant",
                    content: event.reply,
                    trace: event.trace?.length ? event.trace : undefined,
                    timeline: preservedTimeline,
                    timestamp: new Date(),
                    streaming: false,
                    model: routedModel,
                  };
                }
                next.set(key, { ...cur, messages: msgs, thinking: false });
                return next;
              });
            }
            setSessionsRevision((r) => r + 1);
          } else if (event.type === "limit_reached") {
            const banner: Message = {
              role: "assistant",
              content: "",
              kind: "limit",
              limitIterations: event.iterations,
              timestamp: new Date(),
            };
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              const msgs = cur.messages.slice();
              const lastIdx = msgs.length - 1;
              if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                msgs[lastIdx] = banner;
              } else {
                msgs.push(banner);
              }
              next.set(key, { ...cur, messages: msgs, thinking: false });
              return next;
            });
          } else if (event.type === "error") {
            // Map backend reason to the partial-turn banner status so the UI
            // shows an actionable Retry/Continue row instead of a dead-end
            // error string. Unknown reasons fall through as llm_error.
            const reason = event.reason;
            const knownStatuses: NonNullable<Message["partial"]>["status"][] = [
              "interrupted", "cancelled", "iteration_limit",
              "empty_response", "llm_error", "crashed",
              "length", "upstream_timeout",
            ];
            const mapped =
              reason && (knownStatuses as string[]).includes(reason)
                ? (reason as NonNullable<Message["partial"]>["status"])
                : "llm_error";
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              const msgs = [...cur.messages];
              const lastIdx = msgs.length - 1;
              // Attach partial to the existing (possibly streaming) assistant
              // placeholder so its partial content + badges stay visible and
              // the banner renders beneath them.
              if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                msgs[lastIdx] = {
                  ...msgs[lastIdx],
                  streaming: false,
                  // If no content streamed, leave empty — the banner text is
                  // enough. Otherwise keep the partial reply so Continue has
                  // something to build on.
                  partial: { status: mapped, detail: prettifyStreamError(event.detail) },
                };
              } else {
                msgs.push({
                  role: "assistant",
                  content: "",
                  timestamp: new Date(),
                  partial: { status: mapped, detail: prettifyStreamError(event.detail) },
                });
              }
              next.set(key, { ...cur, messages: msgs, thinking: false });
              return next;
            });
          }
        },
        abortController.signal,
        sendModel,
      );
      if (!sawDone && !abortController.signal.aborted) {
        // Server closed the stream without a terminal `done`. Pull
        // persisted state so any partial progress (+ tool badges the
        // backend persisted in `finally`) surfaces in the UI.
        const recoverSid = activeSession ?? sidForPost;
        if (recoverSid) {
          setChatStates((prev) => {
            const next = new Map(prev);
            const cur = next.get(key) ?? emptyState();
            next.set(key, { ...cur, historyLoaded: false, thinking: false });
            return next;
          });
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
        // Network/fetch error — the backend may have persisted partial
        // progress (user msg + partial assistant + tool badges). Try to
        // pull that back before falling back to a raw error banner, so
        // the user sees the work that DID complete.
        const recoverSid = activeSession ?? sidForPost;
        let recovered = false;
        if (recoverSid) {
          try {
            const detail = await getSession(recoverSid);
            if (detail.messages.length > 0) {
              recovered = true;
              // Mark history as unloaded so loadSessionHistory refills it.
              setChatStates((prev) => {
                const next = new Map(prev);
                const cur = next.get(key) ?? emptyState();
                next.set(key, { ...cur, historyLoaded: false, thinking: false });
                return next;
              });
              if (!activeSession) setActiveSession(recoverSid);
              void loadSessionHistory(recoverSid);
            }
          } catch { /* fall through to error banner */ }
        }
        if (!recovered) {
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
      }
    } finally {
      if (abortControllersRef.current.get(key) === abortController) {
        abortControllersRef.current.delete(key);
      }
    }
  }, [activeKey, activeSession, chatStates, patchState, appendMessage, pendingSessionId]);

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
    const abort = abortControllersRef.current.get(key);
    const sidForCancel = activeSession ?? pendingSessionId;

    // Best-effort server cancel (unblocks HITL waits + cancels the turn task).
    fetch(`${import.meta.env.VITE_NEXUS_API ?? "http://localhost:18989"}/chat/${encodeURIComponent(sidForCancel)}/cancel`, {
      method: "POST",
    }).catch(() => {});

    abort?.abort();

    // Flip thinking off and mark the placeholder as stopped.
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key);
      if (!cur) return prev;
      const msgs = cur.messages.slice();
      const lastIdx = msgs.length - 1;
      if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
        const existing = msgs[lastIdx].content;
        msgs[lastIdx] = {
          ...msgs[lastIdx],
          content: existing ? `${existing}\n\n_[stopped by user]_` : "_[stopped by user]_",
          streaming: false,
        };
      }
      next.set(key, { ...cur, messages: msgs, thinking: false });
      return next;
    });
  }, [activeKey, activeSession, pendingSessionId]);

  const dismissLimitBanner = useCallback(() => {
    const key = activeKey;
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key);
      if (!cur) return prev;
      const msgs = cur.messages.filter((m) => m.kind !== "limit");
      next.set(key, { ...cur, messages: msgs });
      return next;
    });
  }, [activeKey]);

  const handleContinue = useCallback(() => {
    dismissLimitBanner();
    // Same hidden-seed / in-place trick the partial-turn Continue uses:
    // the user clicked a button, don't add a "continue" user bubble.
    void send({ text: `${HIDDEN_SEED_MARKER}continue`, inPlace: true });
  }, [dismissLimitBanner, send]);

  const handleContinuePartial = useCallback(
    (_visibleIdx: number) => {
      // Continue **in place** — no "continue" user bubble. The existing
      // partial assistant keeps its content and timeline; its ``partial``
      // flag is cleared and ``streaming`` set to true inside ``send`` so
      // delta/tool events append to the same bubble. A hidden-seed user
      // message tells the backend this is a continuation without polluting
      // the visible chat with filler prompts.
      void send({ text: `${HIDDEN_SEED_MARKER}continue`, inPlace: true });
    },
    [send],
  );

  const handleRetryPartial = useCallback(
    async (visibleIdx: number) => {
      const state = chatStates.get(activeKey) ?? emptyState();
      if (state.thinking) return;
      const visible = state.messages.filter(
        (m) =>
          (m.content ?? "").trim().length > 0 ||
          m.kind === "limit" ||
          (m.timeline ?? []).length > 0 ||
          m.partial != null,
      );
      // Walk back from the clicked assistant to find its preceding user message.
      let userVisibleIdx = -1;
      for (let i = visibleIdx - 1; i >= 0; i--) {
        if (visible[i].role === "user") {
          userVisibleIdx = i;
          break;
        }
      }
      if (userVisibleIdx === -1) return;
      const targetUser = visible[userVisibleIdx];
      const targetAsst = visible[visibleIdx];

      // Drop the partial assistant from the UI so the retry's placeholder
      // / streamed content replaces it. Keep the original user bubble
      // visible — the retry is reusing the same prompt, just re-running it.
      const fullAsstIdx = state.messages.indexOf(targetAsst);
      if (fullAsstIdx === -1) return;
      setChatStates((prev) => {
        const next = new Map(prev);
        const cur = next.get(activeKey) ?? emptyState();
        next.set(activeKey, {
          ...cur,
          messages: cur.messages.slice(0, fullAsstIdx),
        });
        return next;
      });
      // (Skipping server-side truncateSession: UI message index doesn't
      // reliably align with the persisted seq once tool messages are
      // interleaved, so a blind truncate could lose unrelated history.
      // The new turn will still run correctly — loom appends to whatever
      // history the server has. Partial stays in the DB until a future
      // turn overwrites; the UI hides it on reload via the partial-prefix
      // filter plus the retry banner.)

      // Fire the retry with a hidden seed: the backend persists this as a
      // context-only user message (so it can be referenced in future turns)
      // but the UI filters it out. No duplicate user bubble.
      void send(`${HIDDEN_SEED_MARKER}retry: ${targetUser.content}`);
    },
    [activeKey, activeSession, chatStates, send],
  );

  return (
    <div className="app app--layout">
      <Sidebar
        view={view}
        onViewChange={setView}
        activeSessionId={activeSession}
        onSessionSelect={handleSessionSelect}
        onNewChat={handleNewChat}
        onOpenSettings={() => setSettingsOpen(true)}
        sessionsRevision={sessionsRevision}
        onSessionsRevisionBump={() => setSessionsRevision((r) => r + 1)}
        vaultSelectedPath={vaultSelectedPath}
        onVaultSelectPath={setVaultSelectedPath}
        vaultOpenPath={vaultOpenPath}
        onVaultOpenPathHandled={() => setVaultOpenPath(null)}
        onDispatchToChat={handleDispatchToChat}
        onViewEntityGraph={handleViewEntityGraph}
      />

      <div className="app-main">
        <Header onReset={handleNewChat} yoloMode={yoloMode} />
        {backendUp === false && (
          <div
            style={{
              padding: "6px 12px",
              background: "#b91c1c",
              color: "white",
              fontSize: 13,
              textAlign: "center",
            }}
          >
            Backend unreachable — check that <code>nexus serve</code> is running on {" "}
            {import.meta.env.VITE_NEXUS_API ?? "http://localhost:18989"}.
          </div>
        )}

        <main className="app-content">
          <div className="view-pane" style={{ display: view === "chat" ? "flex" : "none" }}>
            <ChatView
              messages={activeState.messages}
              thinking={activeState.thinking}
              input={activeState.input}
              onInputChange={handleInputChange}
              onSend={send}
              onStop={handleStop}
              onContinue={handleContinue}
              onRetryPartial={handleRetryPartial}
              onContinuePartial={handleContinuePartial}
              onDismissLimit={dismissLimitBanner}
              hasModel={hasModel}
              onOpenSettings={() => setSettingsOpen(true)}
              onOpenInVault={handleOpenInVault}
              attachments={activeState.attachments}
              onAttachmentsChange={handleAttachmentsChange}
              onRollback={handleRollback}
              models={availableModels}
              selectedModel={activeState.selectedModel}
              onModelChange={handleModelChange}
            />
          </div>
          <div className="view-pane" style={{ display: view === "vault" ? "flex" : "none" }}>
            <VaultView
              selectedPath={vaultSelectedPath}
              onDispatchToChat={handleDispatchToChat}
              onOpenInChat={handleOpenInChat}
              onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
            />
          </div>
          <div className="view-pane" style={{ display: view === "graph" ? "flex" : "none" }}>
            <UnifiedGraphView
              onOpenSkill={(name) => setOpenSkill(name)}
              onSelectSession={handleSessionSelect}
              graphSourceFilter={graphSourceFilter}
              onGraphSourceFilterHandled={() => setGraphSourceFilter(null)}
              onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
              onStartGraphIndex={handleStartGraphIndex}
            />
          </div>
          <div className="view-pane" style={{ display: view === "insights" ? "flex" : "none" }}>
            {view === "insights" && (
              <InsightsView
                onOpenSession={(sid) => {
                  setView("chat");
                  handleSessionSelect(sid);
                }}
              />
            )}
          </div>
        </main>
      </div>

      <SkillDrawer
        skillName={openSkill === "__list__" ? null : openSkill}
        onClose={() => setOpenSkill(null)}
      />
      <SettingsDrawer
        open={settingsOpen}
        onClose={() => {
          setSettingsOpen(false);
          setSettingsRevision((r) => r + 1);
        }}
      />
      {pendingRequest && (
        <ApprovalDialog
          request={pendingRequest}
          onSubmit={handleApprovalSubmit}
          onTimeout={handleApprovalTimeout}
        />
      )}
    </div>
  );
}
