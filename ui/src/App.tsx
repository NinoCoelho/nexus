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
import BotsPane from "./components/views/BotsPane";
import AdvancedPane from "./components/views/AdvancedPane";

const CalendarView = lazy(() => import("./components/CalendarView"));
const VaultView = lazy(() => import("./components/VaultView"));
const WorkflowView = lazy(() => import("./components/WorkflowView"));
import {
  cancelGraphragIndexFile,
  cancelHitlRequest,
  getSession,
  getSessions,
  graphragIndexFile,
  respondToUserRequest,
} from "./api";
import { fetchBotDirectory } from "./hooks/useBotDirectory";
import { ArrowLeft } from "lucide-react";
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
import TeamChips from "./components/TeamChips";
import TeamModal from "./components/Sidebar/TeamModal";
import MissedTasksModal from "./components/MissedTasksModal";
import "./components/MissedTasksModal.css";
import { useNotificationCenter } from "./hooks/useNotificationCenter";
import { useVoiceAckPlayer } from "./hooks/useVoiceAckPlayer";
import { usePushSubscription } from "./hooks/usePushSubscription";
import { useBackgroundSkillBuilds } from "./hooks/useBackgroundSkillBuilds";
import { useGlobalSubscriptions } from "./hooks/useGlobalSubscriptions";
import { useTranslation } from "react-i18next";
import NotificationBell from "./components/NotificationBell";
import BrowserIndicator from "./components/BrowserIndicator";
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
  const [calendarSelectedPath, setCalendarSelectedPath] = useState<string | null>(
    () => (route.view === "calendar" ? route.path ?? null : null),
  );
  /** Selected kanban board path — shared by the sidebar board list and the Kanban pane. */
  const [kanbanSelectedPath, setKanbanSelectedPath] = useState<string | null>(
    () => (route.view === "kanban" ? route.path ?? null : null),
  );
  /** Selected bot in the Bots view — shared by the sidebar bots list and the
   * workspace pane ("main" = the coordinator/main bot). */
  const [botsSelectedId, setBotsSelectedId] = useState<string | null>(
    () => (route.view === "bots" ? route.path ?? null : null),
  );
  /** True while a bot chat owns the main pane in the Bots view (the
   * workspace hides behind it until the user goes back). */
  const [botsChatOpen, setBotsChatOpen] = useState(false);
  /** Selected app folder in the Apps view — shared by the sidebar list and the Apps pane. */
  const [appsSelectedFolder, setAppsSelectedFolder] = useState<string | null>(
    () => (route.view === "apps" ? route.path ?? null : null),
  );
  const [graphSourceFilter, setGraphSourceFilter] = useState<{ mode: "file" | "folder"; path: string } | null>(null);
  const [pendingGraphIndex, setPendingGraphIndex] = useState<string | null>(null);
  const indexingToastIdRef = useRef<string | null>(null);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  /** Icon-rail mode — shared by the sidebar's own toggle and the Header's
   * maximize button (same persisted key the sidebar used). */
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem("sidebar-collapsed") === "true"; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("sidebar-collapsed", String(sidebarCollapsed)); } catch { /* ignore */ }
  }, [sidebarCollapsed]);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [chatSearchOpen, setChatSearchOpen] = useState(false);
  const [teamEditOpen, setTeamEditOpen] = useState(false);
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
    if (route.view === "calendar" && p && p !== lastRoutePathRef.current) {
      setCalendarSelectedPath(p);
    }
    if (route.view === "bots" && p && p !== lastRoutePathRef.current) {
      setBotsSelectedId(p);
      setBotsChatOpen(false);
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
    chatStates, activeState, activeSession, setActiveSession, setChatStates,
    sessionsRevision, setSessionsRevision,
    pendingAutoSend, pendingNewSession,
    send, handleStop, handleRemoveQueued, handleRollback,
    handleContinuePartial, handleRetryPartial,
    handleInputChange, handleAttachmentsChange, handleModelChange,
    handleSessionSelect: _handleSessionSelect,
    handleNewChat: _handleNewChat,
    handleCompact: _handleCompact, handleRemoveLast,
    handleResumePaused, handleContinueTeam,
  } = chatSession;

  // While a bot chat owns the Bots main pane, the workspace hides behind
  // it. Requires the chat's bot to be the selected one — the active
  // session's project is reliable post-load (loadSessionHistory +
  // first-turn migration stamp it). Unprojected chats belong to "main".
  const activeProjectId = activeState.projectId ?? null;
  const activeBotId = activeProjectId ?? "main";
  const chatInBots =
    view === "bots" &&
    botsChatOpen &&
    activeBotId === botsSelectedId;
  const chatPaneVisible = chatInBots;

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

  // Bot-chat routing: opening any chat keeps the user in the Bots view —
  // the chat takes over the main pane and the sidebar highlights the owning
  // bot. Unprojected chats belong to the "main" bot. The session's project
  // comes from the caller's hint (bot rows/BotsPane know it), the loaded
  // chat state, or a one-off fetch for cross-surface jumps.
  const openSessionLocation = useCallback((projectId: string | null) => {
    setBotsSelectedId(projectId ?? "main");
    setBotsChatOpen(true);
    setView("bots");
  }, [setView]);

  const handleSessionSelect = useCallback((id: string, projectId?: string | null) => {
    // The optimistic placeholder shown while the first turn is in flight
    // shares its id with `pendingSessionId`; clicking it must NOT call into
    // _handleSessionSelect, which would try to load history for a session
    // that doesn't exist yet on the server (and would re-key chat state away
    // from NEW_KEY mid-stream).
    if (pendingNewSession && id === pendingNewSession.id) {
      openSessionLocation(projectId ?? pendingNewSession.project_id ?? null);
      return;
    }
    _handleSessionSelect(id);
    let pid = projectId;
    if (pid === undefined) {
      const st = chatStates.get(id);
      pid = st && st.projectId !== undefined ? st.projectId : undefined;
    }
    if (pid === undefined) {
      // Unknown (jump to a never-opened session) — resolve via the session
      // endpoint, then land. History loads in parallel.
      getSession(id)
        .then((d) => {
          const p = d.project_id ?? null;
          setChatStates((prev) => {
            const cur = prev.get(id);
            if (!cur || cur.projectId !== undefined) return prev;
            const next = new Map(prev);
            next.set(id, { ...cur, projectId: p });
            return next;
          });
          openSessionLocation(p);
        })
        .catch(() => openSessionLocation(null));
      return;
    }
    // Stamp the project onto the chat state immediately so the bots-view
    // chat gate doesn't wait for the history load to land.
    if (pid) {
      setChatStates((prev) => {
        const cur = prev.get(id);
        if (cur?.projectId === pid) return prev;
        const next = new Map(prev);
        next.set(id, { ...(cur ?? { ...emptyState(), historyLoaded: false }), projectId: pid });
        return next;
      });
    }
    openSessionLocation(pid);
  }, [_handleSessionSelect, chatStates, pendingNewSession, openSessionLocation, setChatStates]);

  const handleNewChat = useCallback((projectId?: string | null) => {
    // Guard: some call sites hand this straight to onClick, whose first
    // argument is the MouseEvent — coerce anything that isn't a string id
    // to "unprojected" instead of storing the event as projectId.
    const pid = typeof projectId === "string" && projectId ? projectId : null;
    _handleNewChat(pid);
    clearPendingRequest();
    setBotsSelectedId(pid ?? "main");
    setBotsChatOpen(true);
    setView("bots");
  }, [_handleNewChat, clearPendingRequest, setView]);

  /** Opens a bot's workspace (`#/bots/<id>` or the sidebar ⚙ button) —
   * the workspace, not a chat, owns the main pane. */
  const handleOpenBot = useCallback((id: string | null) => {
    setBotsSelectedId(id);
    setBotsChatOpen(false);
    setView("bots", id);
  }, [setView]);

  // ── Bot mentions & forwarding ─────────────────────────────────────────
  //
  // Clicking an @handle link in an assistant reply opens that bot's chat
  // with the quoting message pre-filled; the forward button sends the
  // quoted context straight into the bot's most recent chat (no view
  // switch — the current conversation stays on screen).

  const resolveBotChat = useCallback(async (botId: string): Promise<string | null> => {
    try {
      const { sessions } = await getSessions(200, 0);
      return sessions.find((s) => s.project_id === botId)?.id ?? null;
    } catch {
      return null;
    }
  }, []);

  const handleMentionBot = useCallback((handle: string, quote: string) => {
    void (async () => {
      const bots = await fetchBotDirectory();
      const bot = bots.find((b) => b.handle.toLowerCase() === handle.toLowerCase());
      if (!bot) return;
      const sid = await resolveBotChat(bot.id);
      if (sid) {
        setChatStates((prev) => {
          const next = new Map(prev);
          const cur = next.get(sid);
          if (cur?.thinking) return prev; // don't fight an in-flight turn
          next.set(sid, { ...(cur ?? emptyState()), input: cur?.input ? `${cur.input}\n\n> ${quote}` : `> ${quote}` });
          return next;
        });
        handleSessionSelect(sid);
      } else {
        handleNewChat(bot.id);
        setChatStates((prev) => {
          const next = new Map(prev);
          const cur = next.get(NEW_KEY);
          next.set(NEW_KEY, { ...(cur ?? emptyState()), input: `> ${quote}` });
          return next;
        });
      }
    })();
  }, [resolveBotChat, handleSessionSelect, handleNewChat, setChatStates]);

  const handleForwardToBot = useCallback((botId: string, handle: string, quote: string) => {
    void (async () => {
      const text = `Context forwarded from another conversation (cc @${handle}):\n\n> ${quote}`;
      const sid = await resolveBotChat(botId);
      if (sid) {
        await chatSession.sendToSession(sid, text);
        toast.success(`Forwarded to @${handle}`, { detail: "The bot got the quoted context in its latest chat." });
      } else {
        // No chat yet — open a fresh one pre-filled so the user just hits send.
        handleNewChat(botId);
        setChatStates((prev) => {
          const next = new Map(prev);
          const cur = next.get(NEW_KEY);
          next.set(NEW_KEY, { ...(cur ?? emptyState()), input: text });
          return next;
        });
        toast.info(`No open chat for @${handle} — started one, ready to send.`);
      }
    })();
  }, [resolveBotChat, chatSession, handleNewChat, setChatStates, toast]);

  /** Opens a hidden internal run session (bot activity) read-only. */
  const handleOpenActivitySession = useCallback((sessionId: string) => {
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = prev.get(sessionId);
      next.set(sessionId, { ...(cur ?? { ...emptyState(), historyLoaded: false }), readOnly: true });
      return next;
    });
    handleSessionSelect(sessionId);
  }, [setChatStates, handleSessionSelect]);

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
    if (sessionId) handleSessionSelect(sessionId);
  }, [handleSessionSelect]);

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
    handleSessionSelect(sessionId);
    setSessionsRevision((r) => r + 1);
  }, [setChatStates, setActiveSession, setSessionsRevision, handleSessionSelect]);

  // Voice acknowledgment playback. The hook handles ack-kind routing
  // (suppress start/progress for background sessions, surface a clickable
  // toast for cross-session completions). The subscription is global —
  // /notifications/events fans `voice_ack` out for every session, so
  // background turns are heard even when the user has navigated away.
  const ackPlayer = useVoiceAckPlayer({
    activeSessionId: activeSession ?? null,
    view: chatPaneVisible ? "chat" : view,
    onJumpToSession: (sid) => handleSessionSelect(sid),
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
    handleSessionSelect(sessionId);
    setSessionsRevision((r) => r + 1);
    void title; // title was set server-side on dispatch
  }, [handleSessionSelect, setChatStates, chatSession, setSessionsRevision, pendingAutoSend]);

  const handleNavigateToSession = useCallback((sessionId: string) => {
    handleSessionSelect(sessionId);
  }, [handleSessionSelect]);

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
      setChatSearchOpen(true);
    }, []),
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
    setView("bots");
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

  const masterSessionId = coordinator.enabled ? coordinator.sessionId : null;

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
         coordinatorSessionId={masterSessionId}
         coordinatorName={coordinator.enabled ? coordinator.name : null}
         collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
         botsSelectedId={botsSelectedId}
         onOpenBot={handleOpenBot}
        appSelectedFolder={appsSelectedFolder}
        onAppSelectFolder={setAppsSelectedFolder}
        kanbanSelectedPath={kanbanSelectedPath}
        onKanbanOpen={handleKanbanOpen}
        calendarSelectedPath={calendarSelectedPath}
        onCalendarSelect={setCalendarSelectedPath}
      />

      <div className="app-main">
        <Header
          onReset={() => handleNewChat()}
          yoloMode={yoloMode}
          onOpenMobileDrawer={() => setMobileDrawerOpen(true)}
          onOpenSettings={handleOpenSettings}
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((c) => !c)}
          statusSlot={
            chatPaneVisible
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
              <BrowserIndicator
                contextKey={activeProjectId ? `project:${activeProjectId}` : "main"}
              />
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
          <div className="view-pane" style={{ display: chatPaneVisible ? "flex" : "none" }}>
            {chatInBots && (
              <div className="project-chat-topbar">
                <button className="project-chat-back" onClick={() => setBotsChatOpen(false)} title="Back to the bot workspace">
                  <ArrowLeft size={13} /> Bot workspace
                </button>
                {activeState.team && (
                  <TeamChips team={activeState.team} onEdit={() => setTeamEditOpen(true)} />
                )}
              </div>
            )}
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
              onNewSession={() => handleNewChat(chatInBots && botsSelectedId !== "main" ? botsSelectedId : null)}
              onRemoveLast={handleRemoveLast}
              onResumePaused={handleResumePaused}
              models={availableModels}
              selectedModel={activeState.selectedModel}
              onModelChange={handleModelChange}
              readOnly={activeState.readOnly ?? false}
              cascadeCapped={activeState.cascadeCapped ?? null}
              onContinueTeam={activeSession ? () => void handleContinueTeam(activeSession) : undefined}
              onMentionBot={handleMentionBot}
              onForwardToBot={handleForwardToBot}
            />
          </div>
          <div className="view-pane" style={{ display: view === "bots" && !chatInBots ? "flex" : "none" }}>
            <KeepMounted active={view === "bots"}>
              <BotsPane
                activeSessionId={activeSession}
                selectedId={botsSelectedId}
                onSelectId={handleOpenBot}
                onSessionSelect={handleSessionSelect}
                onNewChatInBot={handleNewChat}
                onOpenActivitySession={handleOpenActivitySession}
                onOpenInVault={handleOpenInVault}
                refreshKey={sessionsRevision}
                onRefresh={handleSessionsRevisionBump}
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
                onOpenInChat={(sid) => handleSessionSelect(sid)}
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
      <TeamModal
        open={teamEditOpen}
        editSessionId={activeSession}
        editTeam={activeState.team ?? null}
        onClose={() => setTeamEditOpen(false)}
        onUpdated={(team) => {
          if (activeSession) {
            setChatStates((prev) => {
              const next = new Map(prev);
              const cur = next.get(activeSession);
              if (cur) next.set(activeSession, { ...cur, team });
              return next;
            });
          }
          handleSessionsRevisionBump();
        }}
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
