import { useCallback, useEffect, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import MobileTabBar from "./components/MobileTabBar";
import ChatView from "./components/ChatView";
import VaultView from "./components/VaultView";
import InsightsView from "./components/InsightsView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import ApprovalDialog from "./components/ApprovalDialog";
import UnifiedGraphView from "./components/UnifiedGraphView";
import {
  getGraphragIndexStatus,
  graphragIndexFile,
  pingHealth,
} from "./api";
import { IS_CAPACITOR } from "./api/base";
import { useToast } from "./toast/ToastProvider";
import { NEW_KEY, emptyState, freshSessionId, readInitialView } from "./types/chat";
import { useChatSession } from "./hooks/useChatSession";
import { useSettings } from "./hooks/useSettings";
import { useApprovalQueue } from "./hooks/useApprovalQueue";
import { useShortcuts } from "./hooks/useShortcuts";
import { useSessionUsage } from "./hooks/useSessionUsage";
import ShortcutsModal from "./components/ShortcutsModal";
import AgentStatusBar from "./components/AgentStatusBar";

export default function App() {
  const toast = useToast();
  const initial = readInitialView();
  const [view, setView] = useState(initial.view);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** Bumps a vault path into VaultView when user clicks "Open in Vault" from a preview modal. */
  const [vaultOpenPath, setVaultOpenPath] = useState<string | null>(initial.vaultPath);
  /** The currently selected file path in the vault tree (lifted so Sidebar tree + editor share it). */
  const [vaultSelectedPath, setVaultSelectedPath] = useState<string | null>(initial.vaultPath);
  const [graphSourceFilter, setGraphSourceFilter] = useState<{ mode: "file" | "folder"; path: string } | null>(null);
  const [pendingGraphIndex, setPendingGraphIndex] = useState<string | null>(null);
  // Backend-reachability pill. Polls /health every 15s; shows when the
  // server is unreachable so the user can tell "server is down" apart
  // from "model is still thinking". Starts as null (unknown) — never
  // shows the banner on first load before the first ping resolves.
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [chatSearchOpen, setChatSearchOpen] = useState(false);

  // Sync `view` ⇄ URL hash so refresh / share / Capacitor app-resume land on
  // the right tab. Hash is preferred over query string because it's
  // self-contained for static hosting and doesn't fight the existing
  // `?path=` deep link from `readInitialView`.
  useEffect(() => {
    const target = `#/${view}`;
    if (window.location.hash !== target) {
      window.history.replaceState(null, "", target);
    }
  }, [view]);
  useEffect(() => {
    const onHash = () => {
      const m = window.location.hash.match(/^#\/(chat|vault|graph|insights)$/);
      if (m) setView(m[1] as typeof view);
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Dismiss any full-screen overlay (settings, skill drawer, mobile nav)
  // when the user switches top-level views — otherwise on mobile the
  // drawer covers the new view and feels stuck.
  useEffect(() => {
    setSettingsOpen(false);
    setOpenSkill(null);
    setMobileDrawerOpen(false);
  }, [view]);

  useEffect(() => {
    let cancelled = false;
    const tick = () => void pingHealth().then((ok) => { if (!cancelled) setBackendUp(ok); });
    tick();
    const id = setInterval(tick, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const settings = useSettings();
  const { hasModel, availableModels, lastUsedModel, defaultModel, yoloMode, bumpSettingsRevision, persistUsedModel } = settings;

  const chatSession = useChatSession(
    { availableModels, lastUsedModel, defaultModel, persistUsedModel },
    freshSessionId,
  );

  const {
    activeState, activeSession, setActiveSession, setChatStates,
    sessionsRevision, setSessionsRevision,
    pendingAutoSend, pendingSessionId,
    send, handleStop, handleRollback, handleContinue,
    handleContinuePartial, handleRetryPartial, dismissLimitBanner,
    handleInputChange, handleAttachmentsChange, handleModelChange,
    handleSessionSelect: _handleSessionSelect,
    handleNewChat: _handleNewChat,
  } = chatSession;

  // Seed the __new__ slot's selectedModel whenever routing info loads so
  // the model picker is pre-filled on first render.
  useEffect(() => {
    if (availableModels.length === 0) return;
    const isReal = (s: string) => !!s && s !== "auto" && availableModels.includes(s);
    const seed = isReal(lastUsedModel) ? lastUsedModel : (isReal(defaultModel) ? defaultModel : (availableModels[0] ?? ""));
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(NEW_KEY);
      if (cur && !cur.selectedModel) next.set(NEW_KEY, { ...cur, selectedModel: seed });
      return next;
    });
  }, [availableModels, lastUsedModel, defaultModel, setChatStates]);

  // Subscribe to the session's HITL event stream. The UI owns a
  // ``pendingSessionId`` for the not-yet-created "new chat" so the
  // EventSource can open before the first POST — no chicken-and-egg.
  // Once a real ``activeSession`` exists we prefer that. On Capacitor
  // the subscription is a 2s /pending poll — skip it for the phantom
  // pre-session to avoid a perpetual loop before the first message.
  const hitlSessionId = activeSession ?? (IS_CAPACITOR ? null : pendingSessionId);
  const { pendingRequest, handleApprovalSubmit, handleApprovalTimeout, clearPendingRequest } = useApprovalQueue(hitlSessionId);

  const handleSessionSelect = useCallback((id: string) => {
    _handleSessionSelect(id);
    setView("chat");
  }, [_handleSessionSelect]);

  const handleNewChat = useCallback(() => {
    _handleNewChat();
    clearPendingRequest();
    setView("chat");
  }, [_handleNewChat, clearPendingRequest]);

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
  }, [setChatStates, setActiveSession, setSessionsRevision]);

  const handleOpenInChat = useCallback((sessionId: string, seedMessage: string, title: string) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      next.set(sessionId, {
        ...emptyState(),
        historyLoaded: true, // skip GET /sessions — the only "message" is the hidden seed
        selectedModel: chatSession.computeSeedModel(),
      });
      return next;
    });
    pendingAutoSend.current = { sid: sessionId, seed: seedMessage };
    setActiveSession(sessionId);
    setView("chat");
    setSessionsRevision((r) => r + 1);
    void title; // title was set server-side on dispatch
  }, [setChatStates, setActiveSession, setSessionsRevision, pendingAutoSend, chatSession]);

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
              action: { label: "View graph", onClick: () => handleViewEntityGraph("file", capturedPath) },
            });
          } else if (res.status === "error") {
            setPendingGraphIndex(null);
            toast.error("Indexing failed", { detail: res.detail });
          }
        })
        .catch(() => {});
    }, 3000);
    return () => { active = false; clearInterval(interval); };
  }, [pendingGraphIndex, handleViewEntityGraph, toast]);

  useShortcuts({
    onShowHelp: useCallback(() => setShortcutsOpen((v) => !v), []),
    onFocusSearch: useCallback(() => {
      setMobileDrawerOpen(true);
      setTimeout(() => {
        const el = document.getElementById("nx-session-search") as HTMLInputElement | null;
        el?.focus();
        el?.select();
      }, 50);
    }, []),
    onToggleSidebar: useCallback(() => setMobileDrawerOpen((v) => !v), []),
    onNewChat: handleNewChat,
    onFindInChat: useCallback(() => {
      if (view !== "chat") setView("chat");
      setChatSearchOpen(true);
    }, [view]),
    onEscape: useCallback(() => {
      if (shortcutsOpen) setShortcutsOpen(false);
      else if (chatSearchOpen) setChatSearchOpen(false);
      else if (settingsOpen) setSettingsOpen(false);
      else if (mobileDrawerOpen) setMobileDrawerOpen(false);
    }, [shortcutsOpen, chatSearchOpen, settingsOpen, mobileDrawerOpen]),
  });

  const sessionUsage = useSessionUsage(activeSession, activeState.thinking);

  const handleStartGraphIndex = useCallback(async (path: string) => {
    try {
      const res = await graphragIndexFile(path);
      if (res.enabled === false) { toast.error("GraphRAG not configured — add an API key in settings"); return; }
      if (res.reason) { toast.info(res.reason === "empty file" ? "File is empty — nothing to index" : res.reason); return; }
      if (res.queued) { setPendingGraphIndex(path); toast.info(`Indexing started for ${path.split("/").pop() ?? path}…`); }
    } catch (e) {
      toast.error("Failed to start indexing", { detail: e instanceof Error ? e.message : undefined });
    }
  }, [toast]);

  return (
    <div className="app app--layout">
      <Sidebar
        view={view}
        onViewChange={(v) => { setView(v); setMobileDrawerOpen(false); }}
        mobileOpen={mobileDrawerOpen}
        onMobileClose={() => setMobileDrawerOpen(false)}
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
        <Header
          onReset={handleNewChat}
          yoloMode={yoloMode}
          onOpenMobileDrawer={() => setMobileDrawerOpen(true)}
          statusSlot={
            view === "chat"
              ? <AgentStatusBar usage={sessionUsage} thinking={activeState.thinking} />
              : null
          }
        />
        {backendUp === false && (
          <div style={{ padding: "6px 12px", background: "#b91c1c", color: "white", fontSize: 13, textAlign: "center" }}>
            Backend unreachable — check that <code>nexus serve</code> is running on{" "}
            {import.meta.env.VITE_NEXUS_API ?? "http://localhost:18989"}.
          </div>
        )}

        <main className="app-content">
          <div className="view-pane" style={{ display: view === "chat" ? "flex" : "none" }}>
            <ChatView
              messages={activeState.messages}
              thinking={activeState.thinking}
              activeSessionId={activeSession}
              onFeedbackChange={(idx, value) => {
                setChatStates((prev) => {
                  const key = activeSession ?? NEW_KEY;
                  const cur = prev.get(key);
                  if (!cur) return prev;
                  const next = new Map(prev);
                  const visible = cur.messages.filter(
                    (m) =>
                      (m.content ?? "").trim().length > 0 ||
                      m.kind === "limit" ||
                      (m.timeline ?? []).length > 0 ||
                      m.partial != null,
                  );
                  const target = visible[idx];
                  if (!target) return prev;
                  const fullIdx = cur.messages.indexOf(target);
                  if (fullIdx < 0) return prev;
                  const messages = cur.messages.slice();
                  messages[fullIdx] = { ...messages[fullIdx], feedback: value };
                  next.set(key, { ...cur, messages });
                  return next;
                });
              }}
              searchOpen={chatSearchOpen}
              onSearchClose={() => setChatSearchOpen(false)}
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
                onOpenSession={(sid) => { setView("chat"); handleSessionSelect(sid); }}
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
        onClose={() => { setSettingsOpen(false); bumpSettingsRevision(); }}
      />
      {pendingRequest && (
        <ApprovalDialog
          request={pendingRequest}
          onSubmit={handleApprovalSubmit}
          onTimeout={handleApprovalTimeout}
        />
      )}

      <MobileTabBar
        view={view}
        onViewChange={setView}
        onOpenDrawer={() => setMobileDrawerOpen(true)}
      />

      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  );
}
