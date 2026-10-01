import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import MobileTabBar from "./components/MobileTabBar";
import ChatView from "./components/ChatView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import { WizardModal } from "./components/ProviderWizard";
import ApprovalDialog from "./components/ApprovalDialog";
import AppsPane from "./components/views/AppsPane";
import ProjectsPane from "./components/views/ProjectsPane";
import AdvancedPane from "./components/views/AdvancedPane";

const CalendarView = lazy(() => import("./components/CalendarView"));
const VaultView = lazy(() => import("./components/VaultView"));
const WorkflowView = lazy(() => import("./components/WorkflowView"));
import {
  cancelGraphragIndexFile,
  cancelHitlRequest,
  graphragIndexFile,
  respondToUserRequest,
} from "./api";
import { useToast } from "./toast/ToastProvider";
import { NEW_KEY, emptyState, freshSessionId } from "./types/chat";
import { useAppRoute, type AnyView } from "./routes";
import { useChatSession } from "./hooks/useChatSession";
import { useCoordinator } from "./hooks/useCoordinator";
import { useSettings } from "./hooks/useSettings";
import { useApprovalQueue } from "./hooks/useApprovalQueue";
import { useCalendarAlerts } from "./hooks/useCalendarAlerts";
import { useCalendarAlarms } from "./hooks/useCalendarAlarms";
import { useMissedTasks } from "./hooks/useMissedTasks";
import AlarmNotification from "./components/AlarmNotification";
import "./components/AlarmNotification.css";
import MissedTasksModal from "./components/MissedTasksModal";
import "./components/MissedTasksModal.css";
import { useNotificationCenter } from "./hooks/useNotificationCenter";
import { useVoiceAckPlayer } from "./hooks/useVoiceAckPlayer";
import { usePushSubscription } from "./hooks/usePushSubscription";
import { useBackgroundSkillBuilds } from "./hooks/useBackgroundSkillBuilds";
import { useGlobalSubscriptions } from "./hooks/useGlobalSubscriptions";
import { useTranslation } from "react-i18next";
import NotificationBell from "./components/NotificationBell";
import GlobalSpinner from "./components/GlobalSpinner";
import { useShortcuts } from "./hooks/useShortcuts";
import { useRunningJobs } from "./hooks/useRunningJobs";
import { useActiveDownloads } from "./hooks/useActiveDownloads";
import { useSessionUsage } from "./hooks/useSessionUsage";
import ShortcutsModal from "./components/ShortcutsModal";
import AgentStatusBar from "./components/AgentStatusBar";
import UpdateModal from "./components/UpdateModal";
import { type UpdateCheckResult } from "./api/update";

function ViewFallback() {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span className="gs-spinner" style={{ width: 22, height: 22, borderWidth: 3 }} />
    </div>
  );
}

function KeepMounted({ active, children }: { active: boolean; children: ReactNode }) {
  const [mounted, setMounted] = useState(active);
  if (active && !mounted) setMounted(true);
  if (!mounted) return null;
  return <Suspense fallback={<ViewFallback />}>{children}</Suspense>;
}

export default function App() {
  const toast = useToast();
  const { t: tBg } = useTranslation("skillWizard");
  // URL-driven navigation: the hash is the source of truth for the active
  // view (back/forward + deep links work). `setView` is a stable alias so
  // the rest of the component reads naturally.
  const { route, navigate } = useAppRoute();
  const view = route.view;
  const setView = navigate;
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** Bumps a vault path into VaultView when user clicks "Open in Vault" from a preview modal. */
  const [vaultOpenPath, setVaultOpenPath] = useState<string | null>(() => (route.view === "vault" ? route.path ?? null : null));
  /** The currently selected file path in the vault tree (lifted so Sidebar tree + editor share it). */
  const [vaultSelectedPath, setVaultSelectedPath] = useState<string | null>(() => (route.view === "vault" ? route.path ?? null : null));
  /** Currently selected calendar (.md path) inside the Calendar view. Lifted here so view switches preserve it. */
  const [calendarSelectedPath, setCalendarSelectedPath] = useState<string | null>(null);
  /** Selected kanban board path — shared by the sidebar board list and the Kanban pane. */
  const [kanbanSelectedPath, setKanbanSelectedPath] = useState<string | null>(
    () => (route.view === "kanban" ? route.path ?? null : null),
  );
  /** Selected project in the Projects view — shared by the sidebar list and the workspace pane. */
  const [projectsSelectedId, setProjectsSelectedId] = useState<string | null>(null);
  /** Selected app folder in the Apps view — shared by the sidebar list and the Apps pane. */
  const [appsSelectedFolder, setAppsSelectedFolder] = useState<string | null>(
    () => (route.view === "apps" ? route.path ?? null : null),
  );
  const [graphSourceFilter, setGraphSourceFilter] = useState<{ mode: "file" | "folder"; path: string } | null>(null);
  const [pendingGraphIndex, setPendingGraphIndex] = useState<string | null>(null);
  const indexingToastIdRef = useRef<string | null>(null);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [chatSearchOpen, setChatSearchOpen] = useState(false);
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckResult | null>(null);
  const [updateModalOpen, setUpdateModalOpen] = useState(false);

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

  // URL sync, legacy redirects, and back/forward handling live in
  // routes.ts (useAppRoute) — nothing to do here.

  // Dismiss any full-screen overlay (settings, skill drawer, mobile nav)
  // when the user switches top-level views — otherwise on mobile the
  // drawer covers the new view and feels stuck.
  useEffect(() => {
    setSettingsOpen(false);
    setOpenSkill(null);
    setMobileDrawerOpen(false);
  }, [view]);

  // Follow vault deep links (`#/vault/<path>`) whenever they arrive — the
  // initial one is seeded by the useState initializers above.
  const lastRoutePathRef = useRef<string | null>(route.path ?? null);
  useEffect(() => {
    const p = route.path ?? null;
    if (route.view === "vault" && p && p !== lastRoutePathRef.current) {
      setVaultSelectedPath(p);
    }
    if (route.view === "apps" && p && p !== lastRoutePathRef.current) {
      setAppsSelectedFolder(p);
    }
    if (route.view === "kanban" && p && p !== lastRoutePathRef.current) {
      setKanbanSelectedPath(p);
    }
    lastRoutePathRef.current = p;
  }, [route.view, route.path]);

  const settings = useSettings();
  const { settingsRevision } = settings;
  const coordinator = useCoordinator(settingsRevision);
  const { hasModel, availableModels, lastUsedModel, defaultModel, yoloMode, bumpSettingsRevision, persistUsedModel } = settings;

  const chatSession = useChatSession(
    { availableModels, lastUsedModel, defaultModel, persistUsedModel },
    freshSessionId,
  );

  const {
    activeState, activeSession, setActiveSession, setChatStates,
    sessionsRevision, setSessionsRevision,
    pendingAutoSend, pendingNewSession,
    send, handleStop, handleRemoveQueued, handleRollback,
    handleContinuePartial, handleRetryPartial,
    handleInputChange, handleAttachmentsChange, handleModelChange,
    handleSessionSelect: _handleSessionSelect,
    handleNewChat: _handleNewChat,
    handleCompact: _handleCompact, handleRemoveLast,
    handleResumePaused,
  } = chatSession;

  const handleCompact = useCallback(async (options?: { strategy?: string; force_summarize?: boolean }) => {
    try {
      const result = await _handleCompact(options);
      if (!result) return;
      if (result.budget_exceeded) {
        toast.error("Your API budget has been exceeded. Top up your credits or switch providers to continue.");
      } else if (result.still_overflowed) {
        toast.info("Compacted, but the conversation is still too long. Try removing the last message or starting a new session.");
      }
      return result;
    } catch {
      toast.error("Compact failed. Try removing the last message or starting a new session.");
    }
  }, [_handleCompact, toast]);

  // Seed the __new__ slot's selectedModel whenever routing info loads so
  // the model picker is pre-filled on first render.
  useEffect(() => {
    if (availableModels.length === 0) return;
    const isReal = (s: string) => !!s && s !== "auto" && availableModels.includes(s);
    const seed = isReal(lastUsedModel) ? lastUsedModel : (isReal(defaultModel) ? defaultModel : (availableModels[0] ?? ""));
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(NEW_KEY);
      if (cur && (!cur.selectedModel || !isReal(cur.selectedModel))) next.set(NEW_KEY, { ...cur, selectedModel: seed });
      return next;
    });
  }, [availableModels, lastUsedModel, defaultModel, setChatStates]);

  // When availableModels changes (e.g. tier upgrade demo→nexus), sweep all
  // sessions whose selectedModel is no longer available and switch them to
  // the best valid model.
  useEffect(() => {
    if (availableModels.length === 0) return;
    const pick = (s: string | undefined) => {
      if (s && availableModels.includes(s)) return s;
      if (availableModels.includes(defaultModel)) return defaultModel;
      if (availableModels.includes(lastUsedModel)) return lastUsedModel;
      return availableModels[0] ?? "";
    };
    setChatStates((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const [key, state] of next) {
        if (state.selectedModel && !availableModels.includes(state.selectedModel)) {
          next.set(key, { ...state, selectedModel: pick(state.selectedModel) });
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [availableModels, defaultModel, lastUsedModel, setChatStates]);

  // Cross-session HITL subscription. ``useApprovalQueue`` listens on
  // /notifications/events so any agent's question (active chat,
  // backgrounded kanban card, …) pops up regardless of the current
  // view. Recovers pending requests on mount via
  // /notifications/pending so a hard reload mid-question still
  // surfaces the dialog.
  const { pendingRequest, headItem, queueLength, handleApprovalSubmit, handleApprovalTimeout, clearPendingRequest, focusRequest, dropRequest } = useApprovalQueue();

  // Bell + Web Push: durable HITL history visible from any view, plus
  // OS-level notifications when no Nexus tab is open. The push hook
  // registers /sw.js once and (after permission) keeps a live
  // subscription registered with the backend.
  const push = usePushSubscription();
  const notificationCenter = useNotificationCenter();

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

  const handleNewChat = useCallback((projectId?: string | null) => {
    _handleNewChat(projectId);
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

  const { jobs: runningJobs, killJob } = useRunningJobs();
  const { downloads: activeDownloads, cancel: cancelDownload } = useActiveDownloads();

  const handleGoToJob = useCallback((sessionId: string | null, type: string) => {
    if (type === "dream") { setView("dream"); return; }
    if (type === "heartbeat") { setView("heartbeat"); return; }
    if (sessionId) {
      _handleSessionSelect(sessionId);
      setView("chat");
    }
  }, [_handleSessionSelect]);

  const { alarms, dismiss: dismissAlarm, snooze: snoozeAlarm } = useCalendarAlarms({
    onOpenCalendar: handleOpenCalendar,
  });

  const { missed, removeOne: removeMissedOne, dismissAll: dismissAllMissed } = useMissedTasks();

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

  const { backendUp } = useGlobalSubscriptions({
    focusRequest,
    pendingFocusRequestId: notificationCenter.pendingFocusRequestId,
    clearPendingFocus: notificationCenter.clearPendingFocus,
    ackPlayer,
    toast,
    pendingGraphIndex,
    setPendingGraphIndex,
    handleViewEntityGraph,
    indexingToastIdRef,
  });

  const handleOpenInChat = useCallback((sessionId: string, seedMessage: string, title: string, model?: string) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      next.set(sessionId, {
        ...emptyState(),
        historyLoaded: true,
        selectedModel: chatSession.computeSeedModel(model),
      });
      return next;
    });
    pendingAutoSend.current = { sid: sessionId, seed: seedMessage };
    setActiveSession(sessionId);
    setView("chat");
    setSessionsRevision((r) => r + 1);
    void title; // title was set server-side on dispatch
  }, [setChatStates, setActiveSession, setSessionsRevision, pendingAutoSend, chatSession]);

  const handleNavigateToSession = useCallback((sessionId: string) => {
    _handleSessionSelect(sessionId);
    setView("chat");
  }, [_handleSessionSelect]);

  const vaultViewCommon = useMemo(() => ({
    onDispatchToChat: handleDispatchToChat,
    onOpenInChat: handleOpenInChat,
    onNavigateToSession: handleNavigateToSession,
    onViewEntityGraph: (p: string) => handleViewEntityGraph("file", p),
    onOpenCalendar: handleOpenCalendar,
    onOpenInVault: handleOpenInVault,
    onOpenWorkflow: (p: string) => { setVaultSelectedPath(p); setView("workflows"); },
  }), [handleDispatchToChat, handleOpenInChat, handleNavigateToSession, handleViewEntityGraph, handleOpenCalendar, handleOpenInVault]);

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

  // Stable feedback/pin handlers — kept out of the JSX so they don't create a
  // fresh arrow on every App re-render (which would defeat React.memo on
  // AssistantMessage and re-trigger markdown parsing on every keystroke).
  const handleFeedbackChange = useCallback((idx: number, value: "up" | "down" | null) => {
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
  }, [activeSession]);

  const handlePinChange = useCallback((idx: number, pinned: boolean) => {
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
  }, [activeSession]);

  // Stable handlers for Sidebar props — keeps their referential identity
  // across keystroke-driven re-renders so React.memo on Sidebar short-circuits.
  // `navigate` from useAppRoute is referentially stable.
  const handleSidebarViewChange = useCallback((v: AnyView) => { setView(v); setMobileDrawerOpen(false); }, [setView]);
  const handleMobileClose = useCallback(() => setMobileDrawerOpen(false), []);
  const handleOpenSettings = useCallback(() => setSettingsOpen(true), []);
  const handleSessionsRevisionBump = useCallback(() => setSessionsRevision((r) => r + 1), []);
  const handleVaultOpenPathHandled = useCallback(() => setVaultOpenPath(null), []);
  const handleKanbanOpen = useCallback((path: string) => {
    setKanbanSelectedPath(path);
    setView("kanban");
  }, [setView]);
  const handleUpdateAvailable = useCallback((check: UpdateCheckResult) => {
    setUpdateCheck(check);
    setUpdateModalOpen(true);
  }, []);
  const handleSettingsNavigate = useCallback((v: AnyView) => {
    setSettingsOpen(false);
    bumpSettingsRevision();
    setView(v);
  }, [setView, bumpSettingsRevision]);

  return (
    <div className="app app--layout">
      <Sidebar
        view={view}
        onViewChange={handleSidebarViewChange}
        mobileOpen={mobileDrawerOpen}
        onMobileClose={handleMobileClose}
         activeSessionId={activeSession ?? pendingNewSession?.id ?? null}
         onSessionSelect={handleSessionSelect}
         onNewChat={handleNewChat}
         sessionsRevision={sessionsRevision}
        onSessionsRevisionBump={handleSessionsRevisionBump}
        pendingNewSession={pendingNewSession}
        onActiveSessionDeleted={handleNewChat}
        vaultSelectedPath={vaultSelectedPath}
        onVaultSelectPath={setVaultSelectedPath}
        vaultOpenPath={vaultOpenPath}
        onVaultOpenPathHandled={handleVaultOpenPathHandled}
        onDispatchToChat={handleDispatchToChat}
        onViewEntityGraph={handleViewEntityGraph}
        onVisualizeFolderGraph={handleVisualizeFolderGraph}
        onUpdateAvailable={handleUpdateAvailable}
        coordinatorSessionId={coordinator.enabled ? coordinator.sessionId : null}
        projectsSelectedId={projectsSelectedId}
        onProjectsSelect={setProjectsSelectedId}
        appSelectedFolder={appsSelectedFolder}
        onAppSelectFolder={setAppsSelectedFolder}
        kanbanSelectedPath={kanbanSelectedPath}
        onKanbanOpen={handleKanbanOpen}
      />

      <div className="app-main">
        <Header
          onReset={handleNewChat}
          yoloMode={yoloMode}
          onOpenMobileDrawer={() => setMobileDrawerOpen(true)}
          onOpenSettings={handleOpenSettings}
          statusSlot={
            view === "chat"
              ? <AgentStatusBar
                  usage={sessionUsage}
                  thinking={activeState.thinking}
                  selectedModel={activeState.selectedModel}
                  sessionId={activeSession}
                  onCompact={handleCompact}
                  compacting={activeState.thinking}
                />
              : null
          }
          notificationSlot={
            <>
              <GlobalSpinner jobs={runningJobs} downloads={activeDownloads} onKill={killJob} onGoTo={handleGoToJob} onCancelDownload={cancelDownload} />
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
            </>
          }
        />
        {backendUp === false && (
          <div style={{ padding: "6px 12px", background: "var(--bad)", color: "var(--fg-on-status)", fontSize: 13, textAlign: "center" }}>
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
              onFeedbackChange={handleFeedbackChange}
              onPinChange={handlePinChange}
              searchOpen={chatSearchOpen}
              onSearchClose={() => setChatSearchOpen(false)}
              input={activeState.input}
              onInputChange={handleInputChange}
              onSend={send}
              onStop={handleStop}
              onRetryPartial={handleRetryPartial}
              onContinuePartial={handleContinuePartial}
              hasModel={hasModel}
              onOpenSettings={handleOpenSettings}
              onOpenInVault={handleOpenInVault}
              attachments={activeState.attachments}
              onAttachmentsChange={handleAttachmentsChange}
              queued={activeState.queued}
              onRemoveQueued={handleRemoveQueued}
              onRollback={handleRollback}
              onCompact={handleCompact}
              onNewSession={_handleNewChat}
              onRemoveLast={handleRemoveLast}
              onResumePaused={handleResumePaused}
              models={availableModels}
              selectedModel={activeState.selectedModel}
              onModelChange={handleModelChange}
            />
          </div>
          <div className="view-pane" style={{ display: view === "projects" ? "flex" : "none" }}>
            <KeepMounted active={view === "projects"}>
              <ProjectsPane
                activeSessionId={activeSession}
                selectedId={projectsSelectedId}
                onSelectId={setProjectsSelectedId}
                onSessionSelect={handleSessionSelect}
                onNewChatInProject={handleNewChat}
                onOpenInVault={handleOpenInVault}
                refreshKey={sessionsRevision}
              />
            </KeepMounted>
          </div>
          <div className="view-pane" style={{ display: view === "kanban" ? "flex" : "none" }}>
            <KeepMounted active={view === "kanban"}>
              {kanbanSelectedPath ? (
                <VaultView selectedPath={kanbanSelectedPath} {...vaultViewCommon} />
              ) : (
                <div className="view-pane-col">
                  <div className="view-head">
                    <h2 className="view-title">Kanban</h2>
                    <p className="view-sub">Boards are vault markdown files with kanban-plugin frontmatter</p>
                  </div>
                  <div className="view-body">
                    <div className="view-empty">
                      <p>No board selected</p>
                      <p className="view-empty-hint">Pick a board on the left</p>
                    </div>
                  </div>
                </div>
              )}
            </KeepMounted>
          </div>
          <div className="view-pane" style={{ display: view === "apps" ? "flex" : "none" }}>
            <KeepMounted active={view === "apps"}>
              <AppsPane
                selectedFolder={appsSelectedFolder}
                onSelectFolder={setAppsSelectedFolder}
                vaultViewCommon={vaultViewCommon}
                onOpenInVault={handleOpenInVault}
              />
            </KeepMounted>
          </div>
          <div className="view-pane" style={{ display: view === "calendar" ? "flex" : "none" }}>
            <KeepMounted active={view === "calendar"}>
              <CalendarView
                selectedPath={calendarSelectedPath}
                onSelectPath={setCalendarSelectedPath}
                onOpenInChat={handleOpenInChat}
              />
            </KeepMounted>
          </div>
          <div className="view-pane" style={{ display: view === "vault" ? "flex" : "none" }}>
            <KeepMounted active={view === "vault"}>
              <VaultView selectedPath={vaultSelectedPath} {...vaultViewCommon} />
            </KeepMounted>
          </div>
          {(view === "graph" || view === "heartbeat" || view === "dream") && (
            <div className="view-pane" style={{ display: "flex" }}>
              <AdvancedPane
                area={view}
                onAreaChange={(a) => setView(a)}
                onOpenSkill={(name) => setOpenSkill(name)}
                graphSourceFilter={graphSourceFilter}
                onGraphSourceFilterHandled={() => setGraphSourceFilter(null)}
                pendingFolderGraph={pendingFolderGraph}
                onPendingFolderGraphHandled={() => setPendingFolderGraph(null)}
                onViewEntityGraph={(p) => handleViewEntityGraph("file", p)}
                onStartGraphIndex={handleStartGraphIndex}
                onSpawnSession={handleSpawnSessionFromEntity}
                onOpenInChat={(sid) => { setView("chat"); handleSessionSelect(sid); }}
                onOpenInVault={handleOpenInVault}
              />
            </div>
          )}
          <div className="view-pane" style={{ display: view === "workflows" ? "flex" : "none" }}>
            <Suspense fallback={<ViewFallback />}>
              {view === "workflows" && <WorkflowView selectedPath={vaultSelectedPath} onOpen={(p) => { setVaultSelectedPath(p); setView("workflows"); }} />}
            </Suspense>
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
        onNavigateView={handleSettingsNavigate}
      />
      {hasModel === false && (
        <WizardModal
          mode="first-run"
          configuredNames={[]}
          onClose={(result) => {
            if (result.saved) {
              bumpSettingsRevision();
            }
          }}
        />
      )}
      {pendingRequest && (
        <ApprovalDialog
          request={pendingRequest}
          onSubmit={handleApprovalSubmit}
          onTimeout={handleApprovalTimeout}
          onCancel={headItem ? async () => {
            await cancelHitlRequest(headItem.session_id, headItem.request.request_id);
            dropRequest(headItem.request.request_id);
          } : undefined}
          queueLength={queueLength}
        />
      )}

      <MobileTabBar
        view={view}
        onViewChange={setView}
        onOpenDrawer={() => setMobileDrawerOpen(true)}
      />

      <ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />

      {updateModalOpen && updateCheck && (
        <UpdateModal
          check={updateCheck}
          onClose={() => setUpdateModalOpen(false)}
          onSkipped={() => setUpdateModalOpen(false)}
          onInstalled={() => setUpdateModalOpen(false)}
        />
      )}

      <AlarmNotification
        alarms={alarms}
        onDismiss={dismissAlarm}
        onSnooze={snoozeAlarm}
        onOpen={handleOpenCalendar}
      />

      {missed.length > 0 && (
        <MissedTasksModal
          events={missed}
          onFired={removeMissedOne}
          onDismissAll={dismissAllMissed}
          onClose={dismissAllMissed}
        />
      )}
    </div>
  );
}
