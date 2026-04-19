import { useCallback, useState } from "react";
import "./tokens.css";
import "./App.css";
import "./components/Header.css";
import Header from "./components/Header";
import ChatView from "./components/ChatView";
import SkillDrawer from "./components/SkillDrawer";
import SettingsDrawer from "./components/SettingsDrawer";

export interface SessionMeta {
  title: string;
  createdAt: Date;
}

export default function App() {
  const [sessions, setSessions] = useState<Map<string, SessionMeta>>(new Map());
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [chatRevision, setChatRevision] = useState(0);
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);

  const handleReset = () => {
    setActiveSession(null);
    // Bumping the revision remounts ChatView (clearing messages/input).
    // activeSession alone is NOT safe as a key — it changes mid-send when the
    // backend assigns the session id, which would throw away the in-flight
    // conversation.
    setChatRevision((r) => r + 1);
  };

  const handleSessionCreated = useCallback(
    (id: string, title: string) => {
      setSessions((prev) => {
        const next = new Map(prev);
        next.set(id, { title, createdAt: new Date() });
        return next;
      });
      setActiveSession(id);
    },
    [],
  );

  const handleSkillsTouched = useCallback((_names: string[]) => {
    // no-op for now; the live skill-chip pulse UI was removed from ChatView.
    // A future visualization (e.g. activity indicator in the header) can hook here.
  }, []);

  void sessions;

  return (
    <div className="app">
      <Header
        onReset={handleReset}
        onOpenSkills={() => setOpenSkill("__list__")}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <main className="chat-shell">
        <ChatView
          key={chatRevision}
          sessionId={activeSession}
          onSessionCreated={handleSessionCreated}
          onSkillsTouched={handleSkillsTouched}
          onOpenSettings={() => setSettingsOpen(true)}
          settingsRevision={settingsRevision}
        />
      </main>
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
    </div>
  );
}
