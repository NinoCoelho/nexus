import { useCallback, useEffect, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import ChatView, { type Message } from "./components/ChatView";
import VaultView from "./components/VaultView";
import KanbanView from "./components/KanbanView";
import GraphView from "./components/GraphView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import { chatStream, getRouting, getSession, type TraceEvent } from "./api";

type View = "chat" | "vault" | "kanban" | "graph";

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
}

const NEW_KEY = "__new__";
const emptyState = (): ChatState => ({
  messages: [],
  thinking: false,
  input: "",
  historyLoaded: true,
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

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [sessionsRevision, setSessionsRevision] = useState(0);
  const [openSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);
  const [hasModel, setHasModel] = useState<boolean | null>(null);
  /** Bumps a vault path into VaultView when user clicks "Open in Vault" from a preview modal. */
  const [vaultOpenPath, setVaultOpenPath] = useState<string | null>(null);

  const handleOpenInVault = useCallback((path: string) => {
    setVaultOpenPath(path);
    setView("vault");
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
        if (!cancelled) setHasModel((r.available_models?.length ?? 0) > 0);
      })
      .catch(() => {
        if (!cancelled) setHasModel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

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
    // Reset the "__new__" slot so a fresh chat starts clean. Any other
    // session state (including a "thinking" one) is untouched.
    setChatStates((prev) => {
      const next = new Map(prev);
      next.set(NEW_KEY, emptyState());
      return next;
    });
  }, []);

  const handleInputChange = useCallback((v: string) => {
    patchState(activeKey, { input: v });
  }, [activeKey, patchState]);

  const send = useCallback(async () => {
    const key = activeKey;
    const state = chatStates.get(key) ?? emptyState();
    const text = state.input.trim();
    if (!text || state.thinking) return;

    const userMsg: Message = { role: "user", content: text, timestamp: new Date() };
    // Append user message + placeholder assistant message, set thinking.
    const placeholderAsst: Message = { role: "assistant", content: "", trace: [], timestamp: new Date(), streaming: true };
    patchState(key, {
      input: "",
      thinking: true,
      messages: [...state.messages, userMsg, placeholderAsst],
    });

    try {
      await chatStream(
        text,
        activeSession ?? undefined,
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
            const finalAsst: Message = {
              role: "assistant",
              content: event.reply,
              trace: event.trace?.length ? event.trace : undefined,
              timestamp: new Date(),
              streaming: false,
            };

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
      );
    } catch (err) {
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
  }, [activeKey, activeSession, chatStates, patchState, appendMessage]);

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
      />

      <div className="app-main">
        <Header onReset={handleNewChat} />

        <main className="app-content">
          <div className="view-pane" style={{ display: view === "chat" ? "flex" : "none" }}>
            <ChatView
              messages={activeState.messages}
              thinking={activeState.thinking}
              input={activeState.input}
              onInputChange={handleInputChange}
              onSend={send}
              hasModel={hasModel}
              onOpenSettings={() => setSettingsOpen(true)}
              onOpenInVault={handleOpenInVault}
            />
          </div>
          <div className="view-pane" style={{ display: view === "vault" ? "flex" : "none" }}>
            <VaultView
              openPath={vaultOpenPath}
              onOpenPathHandled={() => setVaultOpenPath(null)}
            />
          </div>
          <div className="view-pane" style={{ display: view === "kanban" ? "flex" : "none" }}>
            <KanbanView />
          </div>
          <div className="view-pane" style={{ display: view === "graph" ? "flex" : "none" }}>
            <GraphView />
          </div>
        </main>
      </div>

      <SkillDrawer
        skillName={openSkill === "__list__" ? null : openSkill}
        onClose={() => {}}
      />
      <SettingsDrawer
        open={settingsOpen}
        onClose={() => {
          setSettingsOpen(false);
          setSettingsRevision((r) => r + 1);
        }}
      />
    </div>
  );
}
