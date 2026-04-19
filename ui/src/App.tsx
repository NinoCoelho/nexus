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
  const [pulsingSkills, setPulsingSkills] = useState<Set<string>>(new Set());
  const [openSkill, setOpenSkill] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] = useState(0);

  const handleReset = () => {
    setActiveSession(null);
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

  const handleSkillsTouched = useCallback((names: string[]) => {
    if (!names.length) return;
    setPulsingSkills(new Set(names));
    setTimeout(() => setPulsingSkills(new Set()), 2000);
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
          key={activeSession ?? "__new__"}
          sessionId={activeSession}
          onSessionCreated={handleSessionCreated}
          onSkillsTouched={handleSkillsTouched}
          pulsingSkills={pulsingSkills}
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
