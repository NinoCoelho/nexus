import { useCallback, useEffect, useRef, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import MobileTabBar from "./components/MobileTabBar";
import ChatView from "./components/ChatView";
import CalendarView from "./components/CalendarView";
import VaultView from "./components/VaultView";
import InsightsView from "./components/InsightsView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import { WizardModal } from "./components/ProviderWizard";
import "./components/onboarding/NexusLoginScreen.css";
import ApprovalDialog from "./components/ApprovalDialog";
import UnifiedGraphView from "./components/UnifiedGraphView";
import DatabaseSchemaView from "./components/DatabaseSchemaView";
import DataDashboardView from "./components/DataDashboardView";
import "./components/DatabaseSchemaView/DatabaseSchemaView.css";
import {
  cancelGraphragIndexFile,
  cancelHitlRequest,
  getConfig,
  getGraphragIndexStatus,
  graphragIndexFile,
  pingHealth,
  respondToUserRequest,
} from "./api";
import i18n, { normalizeLanguage } from "./i18n";
import { useToast } from "./toast/ToastProvider";
import { NEW_KEY, emptyState, freshSessionId, readInitialView } from "./types/chat";
import { useChatSession } from "./hooks/useChatSession";
import { useSettings } from "./hooks/useSettings";
import { useNexusAccount } from "./hooks/useNexusAccount";
import { useApprovalQueue } from "./hooks/useApprovalQueue";
import { useCalendarAlerts } from "./hooks/useCalendarAlerts";
import { useNotificationCenter } from "./hooks/useNotificationCenter";
import { useVoiceAckPlayer } from "./hooks/useVoiceAckPlayer";
import { subscribeGlobalNotifications } from "./api/chat";
import { usePushSubscription } from "./hooks/usePushSubscription";
import { useBackgroundSkillBuilds } from "./hooks/useBackgroundSkillBuilds";
import { useTranslation } from "react-i18next";
import NotificationBell from "./components/NotificationBell";
import { useShortcuts } from "./hooks/useShortcuts";
import { useSessionUsage } from "./hooks/useSessionUsage";
import ShortcutsModal from "./components/ShortcutsModal";
import AgentStatusBar from "./components/AgentStatusBar";
import SharedSessionView from "./components/SharedSessionView";

export default function App() {
  const toast = useToast();
  const { t: tBg } = useTranslation("skillWizard");
  // Detect a read-only share-link route before any state setup. Hash routes
  // look like ``#/share/<token>``; that page bypasses the rest of the app
  // entirely, so unauthenticated viewers don't load the chat surface.
  const [shareToken, setShareToken] = useState<string | null>(() => {
    const m = window.location.hash.match(/^#\/share\/(.+)$/);
    return m ? decodeURIComponent(m[1]) : null;
  });
  useEffect(() => {
    const onHash = () => {
      const m = window.location.hash.match(/^#\/share\/(.+)$/);
      setShareToken(m ? decodeURIComponent(m[1]) : null);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const initial = readInitialView();
  const [view, setView] = useState(initial.view);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** Bumps a vault path into VaultView when user clicks "Open in Vault" from a preview modal. */
  const [vaultOpenPath, setVaultOpenPath] = useState<string | null>(initial.vaultPath);
  /** The currently selected file path in the vault tree (lifted so Sidebar tree + editor share it). */
  const [vaultSelectedPath, setVaultSelectedPath] = useState<string | null>(initial.vaultPath);
  /** Currently selected calendar (.md path) inside the Calendar view. Lifted here so view switches preserve it. */
  const [calendarSelectedPath, setCalendarSelectedPath] = useState<string | null>(null);
  /** Currently selected kanban board path inside the Kanban view. */
  const [kanbanSelectedPath, setKanbanSelectedPath] = useState<string | null>(null);
  /** Currently selected data-table path inside the Data view (when drilling into a single table). */
  const [dataSelectedPath, setDataSelectedPath] = useState<string | null>(null);
  /** Folder for which to render an ER diagram inside the Data view. */
  const [dataDiagramFolder, setDataDiagramFolder] = useState<string | null>(null);
  /** Currently selected database (folder) — drives the dashboard view. */
  const [dataSelectedDatabase, setDataSelectedDatabase] = useState<string | null>(null);
  /** Bumped to force DatabaseListPanel to reload (e.g. after delete). */
  const [databaseListRevision, setDatabaseListRevision] = useState(0);
  const [graphSourceFilter, setGraphSourceFilter] = useState<{ mode: "file" | "folder"; path: string } | null>(null);
  const [pendingGraphIndex, setPendingGraphIndex] = useState<string | null>(null);
  const indexingToastIdRef = useRef<string | null>(null);
  // Backend-reachability pill. Polls /health every 15s; shows when the
  // server is unreachable so the user can tell "server is down" apart
  // from "model is still thinking". Starts as null (unknown) — never
  // shows the banner on first load before the first ping resolves.
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [chatSearchOpen, setChatSearchOpen] = useState(false);

  // Wizard background-build tracker — owns SSE subscriptions for any skill
  // builds the user dismissed mid-flight, so they keep running on the
  // server and surface a toast when the agent finishes.
  useBackgroundSkillBuilds({
    toast,
    t: tBg,
    onTryItNow: (skillName) => {
      // Pop the new skill's drawer so the user sees what was built; from
      // there they're one click away from a chat session that uses it.
      setOpenSkill(skillName);
    },
  });

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
      // Migrate legacy #/database deep links to the renamed #/data view.
      if (window.location.hash === "#/database") {
        window.history.replaceState(null, "", "#/data");
      }
      const m = window.location.hash.match(/^#\/(chat|calendar|vault|kanban|data|graph|insights)$/);
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

  // Sync UI language from `~/.nexus/config.toml` once on mount. The fetch
  // interceptor in api/base.ts also reads `localStorage["nexus-language"]` (and
  // `window.__nexusLanguage`) so X-Locale stays in sync without each call site
  // knowing about it. Settings drawer's language picker calls i18n.changeLanguage
  // directly when the user toggles, so this effect doesn't need to refetch.
  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((cfg) => {
        if (cancelled) return;
        const lang = normalizeLanguage(cfg.ui?.language);
        (window as any).__nexusLanguage = lang;
        if (i18n.language !== lang) void i18n.changeLanguage(lang);
      })
      .catch(() => {
        // /config can fail before AuthGate completes; the browser-detector
        // fallback already gave us a reasonable default, so we just bail.
      });
    return () => { cancelled = true; };
  }, []);

  const settings = useSettings();
  const { hasModel, availableModels, lastUsedModel, defaultModel, yoloMode, bumpSettingsRevision, persistUsedModel } = settings;

  // Nexus account — drives the mandatory first-run sign-in gate and the
  // Settings → Nexus tab. ``nexusAccount.status === null`` while the
  // initial /auth/nexus/status fetch is in flight so we don't flash the
  // login screen for already-signed-in users on every page load.
  const nexusAccount = useNexusAccount();

  const chatSession = useChatSession(
    { availableModels, lastUsedModel, defaultModel, persistUsedModel },
    freshSessionId,
  );

  const {
    activeState, activeSession, setActiveSession, setChatStates,
    sessionsRevision, setSessionsRevision,
    pendingAutoSend, pendingNewSession,
    send, handleStop, handleRollback,
    handleContinuePartial, handleRetryPartial,
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

  // Cross-session HITL subscription. ``useApprovalQueue`` listens on
  // /notifications/events so any agent's question (active chat,
  // backgrounded kanban card, …) pops up regardless of the current
  // view. Recovers pending requests on mount via
  // /notifications/pending so a hard reload mid-question still
  // surfaces the dialog.
  const { pendingRequest, queueLength, handleApprovalSubmit, handleApprovalTimeout, clearPendingRequest, focusRequest, dropRequest } = useApprovalQueue();

  // Bell + Web Push: durable HITL history visible from any view, plus
  // OS-level notifications when no Nexus tab is open. The push hook
  // registers /sw.js once and (after permission) keeps a live
  // subscription registered with the backend.
  const push = usePushSubscription();
  const notificationCenter = useNotificationCenter();
  // When the SW relays a click on an OS notification (or a deep link
  // ?respond=<rid>), hop the approval queue to that specific request.
  useEffect(() => {
    const rid = notificationCenter.pendingFocusRequestId;
    if (!rid) return;
    focusRequest(rid);
    notificationCenter.clearPendingFocus();
  }, [notificationCenter.pendingFocusRequestId, focusRequest, notificationCenter]);

  const handleSessionSelect = useCallback((id: string) => {
    // The optimistic placeholder shown while the first turn is in flight
    // shares its id with `pendingSessionId`; clicking it must NOT call into
    // _handleSessionSelect, which would try to load history for a session
    // that doesn't exist yet on the server (and would re-key chat state away
    // from NEW_KEY mid-stream).
    if (pendingNewSession && id === pendingNewSession.id) {
      setView("chat");
      return;
    }
    _handleSessionSelect(id);
    setView("chat");
  }, [_handleSessionSelect, pendingNewSession]);

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

  const handleOpenCalendar = useCallback((path: string) => {
    setCalendarSelectedPath(path);
    setView("calendar");
  }, []);

  useCalendarAlerts({ onOpenCalendar: handleOpenCalendar });

  const handleViewEntityGraph = useCallback((mode: "file" | "folder", path: string) => {
    setGraphSourceFilter({ mode, path });
    setView("graph");
  }, []);

  const [pendingFolderGraph, setPendingFolderGraph] = useState<string | null>(null);
  const handleVisualizeFolderGraph = useCallback((path: string) => {
    setPendingFolderGraph(path);
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

  // Voice acknowledgment playback. The hook handles ack-kind routing
  // (suppress start/progress for background sessions, surface a clickable
  // toast for cross-session completions). The subscription is global —
  // /notifications/events fans `voice_ack` out for every session, so
  // background turns are heard even when the user has navigated away.
  const ackPlayer = useVoiceAckPlayer({
    activeSessionId: activeSession ?? null,
    view,
    onJumpToSession: (sid) => {
      setActiveSession(sid);
      setView("chat");
    },
  });
  const { t: tSettings } = useTranslation("settings");
  useEffect(() => {
    const sub = subscribeGlobalNotifications((sessionId, event) => {
      if (event.kind === "voice_ack") {
        ackPlayer.handle(sessionId, event.data);
      } else if (event.kind === "nexus_tier_changed") {
        const upgraded =
          !event.data.from_models.includes("nexus")
          && event.data.to_models.includes("nexus");
        const downgraded =
          event.data.from_models.includes("nexus")
          && !event.data.to_models.includes("nexus");
        if (upgraded) {
          toast.success(tSettings("settings:nexus.tierChanged.upgraded"));
        } else if (downgraded) {
          toast.info(tSettings("settings:nexus.tierChanged.downgraded"));
        }
        // Refresh settings so the Default model strip + ModelsTab pick
        // up the new registry contents.
        bumpSettingsRevision();
      }
    });
    return () => sub.close();
  }, [ackPlayer, toast, tSettings, bumpSettingsRevision]);

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
          const name = capturedPath.split("/").pop() ?? capturedPath;
          if (res.status === "indexing") {
            const total = res.total_chunks ?? 0;
            const done = res.processed_chunks ?? 0;
            const pct = total > 0 ? Math.round((done / total) * 100) : null;
            const detail = total > 0
              ? `${done} / ${total} chunks${pct !== null ? ` (${pct}%)` : ""}`
              : "Chunking…";
            if (indexingToastIdRef.current) {
              toast.update(indexingToastIdRef.current, { detail });
            }
          } else if (res.status === "done") {
            const n = res.node_count ?? res.nodes?.length ?? 0;
            if (indexingToastIdRef.current) { toast.dismiss(indexingToastIdRef.current); indexingToastIdRef.current = null; }
            setPendingGraphIndex(null);
            toast.success(`Indexing complete — ${n} entit${n === 1 ? "y" : "ies"} found for ${name}`, {
              duration: 8000,
              action: { label: "View graph", onClick: () => handleViewEntityGraph("file", capturedPath) },
            });
          } else if (res.status === "cancelled") {
            if (indexingToastIdRef.current) { toast.dismiss(indexingToastIdRef.current); indexingToastIdRef.current = null; }
            setPendingGraphIndex(null);
            toast.info(`Indexing cancelled for ${name}`);
          } else if (res.status === "error") {
            if (indexingToastIdRef.current) { toast.dismiss(indexingToastIdRef.current); indexingToastIdRef.current = null; }
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

  const handleSpawnSessionFromEntity = useCallback((entityId: number, entityName: string) => {
    void entityId;
    _handleNewChat();
    clearPendingRequest();
    const seed = `Tell me about "${entityName}" — what do we know, where it appears, and how it connects to other things in my knowledge.`;
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(NEW_KEY);
      next.set(NEW_KEY, {
        ...(cur ?? emptyState()),
        input: seed,
      });
      return next;
    });
    setView("chat");
  }, [_handleNewChat, clearPendingRequest, setChatStates]);

  const handleStartGraphIndex = useCallback(async (path: string) => {
    try {
      const res = await graphragIndexFile(path);
      if (res.enabled === false) { toast.error("GraphRAG not configured — add an API key in settings"); return; }
      if (res.reason) { toast.info(res.reason === "empty file" ? "File is empty — nothing to index" : res.reason); return; }
      if (res.queued) {
        setPendingGraphIndex(path);
        const name = path.split("/").pop() ?? path;
        indexingToastIdRef.current = toast.info(
          `Indexing ${name}…`,
          {
            detail: "Starting…",
            duration: 0,
            action: {
              label: "Cancel",
              keepOpen: true,
              onClick: () => {
                cancelGraphragIndexFile(path).catch(() => {});
                if (indexingToastIdRef.current) {
                  toast.update(indexingToastIdRef.current, { detail: "Cancelling…", action: undefined });
                }
              },
            },
          },
        );
      }
    } catch (e) {
      toast.error("Failed to start indexing", { detail: e instanceof Error ? e.message : undefined });
    }
  }, [toast]);

  if (shareToken) {
    return <SharedSessionView token={shareToken} />;
  }

  return (
    <div className="app app--layout">
      <Sidebar
        view={view}
        onViewChange={(v) => { setView(v); setMobileDrawerOpen(false); }}
        mobileOpen={mobileDrawerOpen}
        onMobileClose={() => setMobileDrawerOpen(false)}
        activeSessionId={activeSession ?? pendingNewSession?.id ?? null}
        onSessionSelect={handleSessionSelect}
        onNewChat={handleNewChat}
        onOpenSettings={() => setSettingsOpen(true)}
        sessionsRevision={sessionsRevision}
        onSessionsRevisionBump={() => setSessionsRevision((r) => r + 1)}
        pendingNewSession={pendingNewSession}
        onActiveSessionDeleted={handleNewChat}
        vaultSelectedPath={vaultSelectedPath}
        onVaultSelectPath={setVaultSelectedPath}
        vaultOpenPath={vaultOpenPath}
        onVaultOpenPathHandled={() => setVaultOpenPath(null)}
        onDispatchToChat={handleDispatchToChat}
        onViewEntityGraph={handleViewEntityGraph}
        onVisualizeFolderGraph={handleVisualizeFolderGraph}
        kanbanSelectedPath={kanbanSelectedPath}
        onKanbanOpen={(path) => { setKanbanSelectedPath(path); setView("kanban"); }}
        databaseSelectedPath={dataSelectedPath}
        databaseSelectedFolder={dataSelectedDatabase}
        databaseListRevision={databaseListRevision}
        onDatabaseOpen={(path) => {
          setDataSelectedPath(path);
          setDataDiagramFolder(null);
          // Pin the parent folder as the active database so navigating back
          // (clearing the path) lands on the correct dashboard.
          const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
          setDataSelectedDatabase(parent);
          setView("data");
        }}
        onDatabaseSelectFolder={(folder) => {
          setDataSelectedDatabase(folder);
          setDataSelectedPath(null);
          setDataDiagramFolder(null);
          setView("data");
        }}
        onDatabaseOpenDiagram={(folder) => {
          setDataDiagramFolder(folder);
          setDataSelectedPath(null);
          setDataSelectedDatabase(folder);
          setView("data");
        }}
      />

      <div className="app-main">
        <Header
          onReset={handleNewChat}
          yoloMode={yoloMode}
          onOpenMobileDrawer={() => setMobileDrawerOpen(true)}
          statusSlot={
            view === "chat"
              ? <AgentStatusBar
                  usage={sessionUsage}
                  thinking={activeState.thinking}
                  selectedModel={activeState.selectedModel}
                />
              : null
          }
          notificationSlot={
            <NotificationBell
              history={notificationCenter.history}
              pendingCount={notificationCenter.pendingCount}
              pushPermission={push.permission}
              pushSubscribed={push.subscribed}
              onRequestPushPermission={() => { void push.requestPermission(); }}
              onRefresh={notificationCenter.refresh}
              onSelectPending={focusRequest}
              onJumpToChat={handleSessionSelect}
              onCancel={async (sid, rid) => {
                await cancelHitlRequest(sid, rid);
                dropRequest(rid);
              }}
              onAnswer={async (sid, rid, answer) => {
                await respondToUserRequest(sid, rid, answer);
                dropRequest(rid);
              }}
            />
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
              onPinChange={(idx, pinned) => {
                setChatStates((prev) => {
                  const key = activeSession ?? NEW_KEY;
                  const cur = prev.get(key);
                  if (!cur) return prev;
                  const next = new Map(prev);
                  const visible = cur.messages.filter(
                    (m) =>
                      (m.content ?? "").trim().length > 0 ||
                      (m.timeline ?? []).length > 0 ||
                      m.partial != null,
                  );
                  const target = visible[idx];
                  if (!target) return prev;
                  const fullIdx = cur.messages.indexOf(target);
                  if (fullIdx < 0) return prev;
                  const messages = cur.messages.slice();
                  messages[fullIdx] = { ...messages[fullIdx], pinned };
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
              onRetryPartial={handleRetryPartial}
              onContinuePartial={handleContinuePartial}
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
          <div className="view-pane" style={{ display: view === "calendar" ? "flex" : "none" }}>
            <CalendarView
              selectedPath={calendarSelectedPath}
              onSelectPath={setCalendarSelectedPath}
              onOpenInChat={handleOpenInChat}
            />
          </div>
          <div className="view-pane" style={{ display: view === "vault" ? "flex" : "none" }}>
            <VaultView
              selectedPath={vaultSelectedPath}
              onDispatchToChat={handleDispatchToChat}
              onOpenInChat={handleOpenInChat}
              onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
              onOpenCalendar={handleOpenCalendar}
              onOpenInVault={handleOpenInVault}
            />
          </div>
          <div className="view-pane" style={{ display: view === "kanban" ? "flex" : "none" }}>
            {kanbanSelectedPath ? (
              <VaultView
                selectedPath={kanbanSelectedPath}
                onDispatchToChat={handleDispatchToChat}
                onOpenInChat={handleOpenInChat}
                onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
                onOpenCalendar={handleOpenCalendar}
                onOpenInVault={handleOpenInVault}
              />
            ) : (
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--fg-faint)", fontSize: 13 }}>
                Pick a board on the left.
              </div>
            )}
          </div>
          <div className="view-pane" style={{ display: view === "data" ? "flex" : "none" }}>
            {dataDiagramFolder !== null ? (
              <DatabaseSchemaView
                folder={dataDiagramFolder}
                onClose={() => setDataDiagramFolder(null)}
              />
            ) : dataSelectedPath ? (
              <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
                {dataSelectedDatabase !== null && (
                  <div style={{ padding: "6px 16px", borderBottom: "1px solid var(--bg-soft)", fontSize: 12 }}>
                    <button
                      className="dt-action-btn"
                      onClick={() => setDataSelectedPath(null)}
                      title="Back to dashboard"
                    >
                      ← Back to dashboard
                    </button>
                  </div>
                )}
                <VaultView
                  selectedPath={dataSelectedPath}
                  onDispatchToChat={handleDispatchToChat}
                  onOpenInChat={handleOpenInChat}
                  onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
                  onOpenCalendar={handleOpenCalendar}
                  onOpenTable={(p) => {
                    setDataSelectedPath(p);
                    setDataDiagramFolder(null);
                    // Pin the parent folder so "Back to dashboard" still works.
                    const parent = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
                    setDataSelectedDatabase(parent);
                  }}
                />
              </div>
            ) : dataSelectedDatabase !== null ? (
              <DataDashboardView
                folder={dataSelectedDatabase}
                onOpenTable={(p) => setDataSelectedPath(p)}
                onOpenDiagram={(f) => { setDataDiagramFolder(f); setDataSelectedPath(null); }}
                onAfterDelete={() => {
                  setDataSelectedDatabase(null);
                  setDataSelectedPath(null);
                  setDataDiagramFolder(null);
                  setDatabaseListRevision((n) => n + 1);
                }}
                onOpenInVault={handleOpenInVault}
              />
            ) : (
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--fg-faint)", fontSize: 13 }}>
                Pick a database on the left to open its dashboard.
              </div>
            )}
          </div>
          <div className="view-pane" style={{ display: view === "graph" ? "flex" : "none" }}>
            <UnifiedGraphView
              onOpenSkill={(name) => setOpenSkill(name)}
              onSelectSession={handleSessionSelect}
              graphSourceFilter={graphSourceFilter}
              onGraphSourceFilterHandled={() => setGraphSourceFilter(null)}
              pendingFolderGraph={pendingFolderGraph}
              onPendingFolderGraphHandled={() => setPendingFolderGraph(null)}
              onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
              onStartGraphIndex={handleStartGraphIndex}
              onSpawnSession={handleSpawnSessionFromEntity}
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
      {hasModel === false && (
        <WizardModal
          mode="first-run"
          configuredNames={[]}
          onClose={(result) => {
            if (result.saved) {
              bumpSettingsRevision();
              void nexusAccount.reload();
            }
          }}
        />
      )}
      {pendingRequest && (
        <ApprovalDialog
          request={pendingRequest}
          onSubmit={handleApprovalSubmit}
          onTimeout={handleApprovalTimeout}
          queueLength={queueLength}
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
