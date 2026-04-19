import { useCallback, useEffect, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import ChatView, { type Message } from "./components/ChatView";
import VaultView from "./components/VaultView";
import KanbanView from "./components/KanbanView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import { getRouting, getSession, postChat } from "./api";

type View = "chat" | "vault" | "kanban";

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

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [sessionsRevision, setSessionsRevision] = useState(0);
  const [openSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);
  const [hasModel, setHasModel] = useState<boolean | null>(null);

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
    patchState(key, {
      input: "",
      thinking: true,
      messages: [...state.messages, userMsg],
    });

    try {
      const res = await postChat(text, activeSession ?? undefined);
      const assistantMsg: Message = {
        role: "assistant",
        content: res.reply,
        trace: res.trace?.length ? res.trace : undefined,
        timestamp: new Date(),
      };

      if (!activeSession) {
        // First send of a new chat: the server assigned a session id.
        // Move "__new__" state under the real id and reset "__new__".
        setChatStates((prev) => {
          const next = new Map(prev);
          const fresh = next.get(NEW_KEY) ?? emptyState();
          next.set(res.session_id, {
            messages: [...fresh.messages, assistantMsg],
            thinking: false,
            input: "",
            historyLoaded: true,
          });
          next.set(NEW_KEY, emptyState());
          return next;
        });
        setActiveSession(res.session_id);
      } else {
        patchState(activeSession, {
          thinking: false,
          messages: [...(chatStates.get(activeSession)?.messages ?? state.messages), userMsg, assistantMsg].filter(
            // de-dup userMsg in case it was already appended above
            (m, i, arr) => arr.findIndex((x) => x === m) === i,
          ),
        });
        // Simpler/safer: just append the assistant message and flip thinking.
        appendMessage(activeSession, assistantMsg);
        patchState(activeSession, { thinking: false });
      }
      setSessionsRevision((r) => r + 1);
    } catch (err) {
      const errMsg: Message = {
        role: "assistant",
        content: `Error: ${err instanceof Error ? err.message : "request failed"}`,
        timestamp: new Date(),
      };
      appendMessage(activeSession ?? NEW_KEY, errMsg);
      patchState(activeSession ?? NEW_KEY, { thinking: false });
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
            />
          </div>
          <div className="view-pane" style={{ display: view === "vault" ? "flex" : "none" }}>
            <VaultView />
          </div>
          <div className="view-pane" style={{ display: view === "kanban" ? "flex" : "none" }}>
            <KanbanView />
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
