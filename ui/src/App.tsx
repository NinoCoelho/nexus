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
  putRouting,
  respondToUserRequest,
  fetchPendingRequest,
  subscribeSessionEvents,
  truncateSession,
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

export default function App() {
  const toast = useToast();
  const [view, setView] = useState<View>("chat");
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [sessionsRevision, setSessionsRevision] = useState(0);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);
  const [hasModel, setHasModel] = useState<boolean | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [lastUsedModel, setLastUsedModel] = useState<string>("");
  /** Bumps a vault path into VaultView when user clicks "Open in Vault" from a preview modal. */
  const [vaultOpenPath, setVaultOpenPath] = useState<string | null>(null);
  /** The currently selected file path in the vault tree (lifted so Sidebar tree + editor share it). */
  const [vaultSelectedPath, setVaultSelectedPath] = useState<string | null>(null);
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
          setLastUsedModel(lum);
          // Seed the __new__ slot with last used model (or first available)
          setChatStates((prev) => {
            const next = new Map(prev);
            const cur = next.get(NEW_KEY);
            if (cur && !cur.selectedModel) {
              const seed = lum || (r.available_models?.[0] ?? "");
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
    async (answer: string) => {
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
      const msgs: Message[] = detail.messages
        .filter((m) => (m.role === "user" || m.role === "assistant") && (m.content ?? "").trim().length > 0)
        .map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
          timestamp: parseHistoryTimestamp(m.created_at),
        }));
      setChatStates((prev) => {
        const next = new Map(prev);
        const cur = next.get(id);
        // Don't clobber in-flight state: if thinking or local-only messages
        // exist for this session already, preserve them; only seed history
        // for sessions we haven't loaded yet.
        if (cur && cur.historyLoaded) return prev;
        next.set(id, {
          messages: msgs,
          thinking: cur?.thinking ?? false,
          input: cur?.input ?? "",
          historyLoaded: true,
          attachments: cur?.attachments ?? [],
        });
        return next;
      });
    } catch {
      patchState(id, { historyLoaded: true });
    }
  }, [patchState]);

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
      next.set(NEW_KEY, {
        ...emptyState(),
        selectedModel: lastUsedModel || availableModels[0] || "",
      });
      return next;
    });
    setPendingSessionId(freshSessionId());
    setPendingRequest(null);
    setPendingRequestSession(null);
  }, [lastUsedModel, availableModels]);

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
    const overrideText = typeof override === "string" ? override : undefined;
    const rawText = (overrideText ?? state.input).trim();
    const hasAttachments = state.attachments.length > 0;
    if ((!rawText && !hasAttachments) || state.thinking) return;

    let text = rawText;
    if (hasAttachments) {
      const refs = state.attachments.map((a) => `[${a.name}](vault://${a.vaultPath})`).join("\n");
      text = text ? `${text}\n\n${refs}` : refs;
    }

    const userMsg: Message = { role: "user", content: text, timestamp: new Date(), attachments: hasAttachments ? [...state.attachments] : undefined };
    const placeholderAsst: Message = { role: "assistant", content: "", trace: [], timestamp: new Date(), streaming: true };
    patchState(key, {
      input: "",
      thinking: true,
      attachments: [],
      messages: [...state.messages, userMsg, placeholderAsst],
    });

    // For a new chat, send our client-side session id so the HITL
    // EventSource (opened on that id) and the backend's session
    // agree. For an existing chat, keep the activeSession id.
    const sidForPost = activeSession ?? pendingSessionId;

    const abortController = new AbortController();
    abortControllersRef.current.set(key, abortController);

    const model = state.selectedModel || "auto";

    try {
      await chatStream(
        text,
        sidForPost,
        (event) => {
          if (event.type === "delta") {
            // Append delta text to the last assistant message.
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              const msgs = [...cur.messages];
              const lastIdx = msgs.length - 1;
              if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                msgs[lastIdx] = { ...msgs[lastIdx], content: msgs[lastIdx].content + event.text };
              }
              next.set(key, { ...cur, messages: msgs });
              return next;
            });
          } else if (event.type === "tool") {
            // Consolidate: if result_preview is present, patch the most recent
            // matching trace entry that has no result; else append a new entry.
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              const msgs = [...cur.messages];
              const lastIdx = msgs.length - 1;
              if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
                const prevTrace = msgs[lastIdx].trace ?? [];
                let newTrace: TraceEvent[];
                if (event.result_preview != null) {
                  // Find last entry with matching name and no result → patch it.
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
                msgs[lastIdx] = { ...msgs[lastIdx], trace: newTrace };
              }
              next.set(key, { ...cur, messages: msgs });
              return next;
            });
          } else if (event.type === "done") {
            const routedModel = event.model;
            const finalAsst: Message = {
              role: "assistant",
              content: event.reply,
              trace: event.trace?.length ? event.trace : undefined,
              timestamp: new Date(),
              streaming: false,
              model: routedModel,
            };

            // Persist last_used_model
            const usedModel = state.selectedModel || routedModel || "";
            if (usedModel) {
              putRouting({ last_used_model: usedModel }).catch(() => {});
              setLastUsedModel(usedModel);
            }

            if (!activeSession) {
              // First message — migrate __new__ to the real session id.
              setChatStates((prev) => {
                const next = new Map(prev);
                const fresh = next.get(NEW_KEY) ?? emptyState();
                // Replace placeholder with final content.
                const msgs = fresh.messages.slice(0, -1).concat(finalAsst);
                next.set(event.session_id, {
                  messages: msgs,
                  thinking: false,
                  input: "",
                  historyLoaded: true,
                  attachments: [],
                  selectedModel: fresh.selectedModel,
                });
                next.set(NEW_KEY, emptyState());
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
                  msgs[lastIdx] = finalAsst;
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
            const errMsg: Message = {
              role: "assistant",
              content: prettifyStreamError(event.detail),
              timestamp: new Date(),
            };
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(key) ?? emptyState();
              // Replace placeholder with error message.
              const msgs = cur.messages.slice(0, -1).concat(errMsg);
              next.set(key, { ...cur, messages: msgs, thinking: false });
              return next;
            });
          }
        },
        abortController.signal,
        model,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User clicked Stop; the UI was already updated by handleStop.
      } else {
        // Network/fetch error — replace placeholder with error message.
        const errMsg: Message = {
          role: "assistant",
          content: `Error: ${err instanceof Error ? err.message : "request failed"}`,
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
    } finally {
      if (abortControllersRef.current.get(key) === abortController) {
        abortControllersRef.current.delete(key);
      }
    }
  }, [activeKey, activeSession, chatStates, patchState, appendMessage, pendingSessionId]);

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
    send("continue");
  }, [dismissLimitBanner, send]);

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
            <VaultView selectedPath={vaultSelectedPath} onDispatchToChat={handleDispatchToChat} onViewEntityGraph={(p) => handleViewEntityGraph("file", p)} />
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
            {view === "insights" && <InsightsView />}
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
