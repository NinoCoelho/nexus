import { useCallback, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import Sidebar from "./components/Sidebar";
import ChatView from "./components/ChatView";
import VaultView from "./components/VaultView";
import KanbanView from "./components/KanbanView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";
import { getSession, type SessionMessage } from "./api";

type View = "chat" | "vault" | "kanban";

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [chatRevision, setChatRevision] = useState(0);
  const [sessionsRevision, setSessionsRevision] = useState(0);
  const [sessionHistory, setSessionHistory] = useState<SessionMessage[] | undefined>(undefined);
  const [openSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);

  const handleReset = useCallback(() => {
    setActiveSession(null);
    setSessionHistory(undefined);
    setChatRevision((r) => r + 1);
    setView("chat");
  }, []);

  const handleSessionCreated = useCallback((id: string) => {
    setActiveSession(id);
    setSessionsRevision((r) => r + 1);
  }, []);

  const handleSessionSelect = useCallback(async (id: string) => {
    try {
      const detail = await getSession(id);
      setSessionHistory(detail.messages);
      setActiveSession(id);
      setChatRevision((r) => r + 1);
      setView("chat");
    } catch {
      // If fetch fails, still switch to empty chat for that session
      setSessionHistory(undefined);
      setActiveSession(id);
      setChatRevision((r) => r + 1);
      setView("chat");
    }
  }, []);

  const handleNewChat = useCallback(() => {
    setActiveSession(null);
    setSessionHistory(undefined);
    setChatRevision((r) => r + 1);
    setView("chat");
  }, []);

  const handleSessionsChanged = useCallback(() => {
    setSessionsRevision((r) => r + 1);
  }, []);

  const handleSkillsTouched = useCallback((_names: string[]) => {
    // no-op for now
  }, []);

  return (
    <div className="app app--layout">
      <Sidebar
        view={view}
        onViewChange={setView}
        activeSessionId={activeSession}
        onSessionSelect={(id) => void handleSessionSelect(id)}
        onNewChat={handleNewChat}
        onOpenSettings={() => setSettingsOpen(true)}
        sessionsRevision={sessionsRevision}
      />

      <div className="app-main">
        <Header onReset={handleReset} />

        <main className="app-content">
          {view === "chat" && (
            <ChatView
              key={chatRevision}
              sessionId={activeSession}
              onSessionCreated={(id, _title) => handleSessionCreated(id)}
              onSkillsTouched={handleSkillsTouched}
              onOpenSettings={() => setSettingsOpen(true)}
              settingsRevision={settingsRevision}
              initialHistory={sessionHistory}
              onSessionsChanged={handleSessionsChanged}
            />
          )}
          {view === "vault" && <VaultView />}
          {view === "kanban" && <KanbanView />}
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
