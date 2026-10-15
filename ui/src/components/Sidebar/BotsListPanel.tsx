/**
 * BotsListPanel — the sidebar's always-visible bots section. Coordinator
 * ("main") pinned topmost (its chats are the unprojected ones), then every
 * bot sorted by updated_at desc. Row click always opens the bot's own most
 * recent chat (or a fresh composer when it has none) — team/group sessions
 * live under the lead bot's project but never hijack the primary click.
 * The chevron is a separate toggle that expands the nested session rows
 * (side conversations + the bot's groups). The ⚙ button opens the bot's
 * workspace without expanding chats.
 */

import { useEffect, useMemo, useState } from "react";
import { getBots, type Bot } from "../../api";
import type { SessionSummary } from "../../api";
import SessionItem from "./SessionItem";
import { loadTeamsCollapsed, TEAMS_COLLAPSED_KEY } from "./utils";

interface Props {
  refreshKey: number;
  /** All active sessions (Sidebar's list, optimistic placeholder merged). */
  sessions: SessionSummary[];
  activeSessionId: string | null;
  /** Bot whose workspace is open in the Bots view — highlights the row. */
  selectedBotId: string | null;
  coordinatorSessionId: string | null;
  coordinatorName?: string | null;
  onSessionSelect: (id: string, projectId?: string | null) => void;
  onNewChat: (projectId?: string | null) => void;
  onOpenBot: (id: string) => void;
  onNewBot: () => void;
  /** Opens the team (group chat) creation modal. */
  onNewTeam: () => void;
  /** Opens the delete-bot confirm (bot rows only, never the coordinator). */
  onDeleteBot: (bot: Bot) => void;
  renamingId: string | null;
  renameValue: string;
  toVaultBusy: Set<string>;
  onContextMenu: (e: React.MouseEvent, id: string) => void;
  onMenuBtnClick: (e: React.MouseEvent, id: string) => void;
  onTitleDoubleClick: (e: React.MouseEvent, id: string, title: string) => void;
  onRenameChange: (v: string) => void;
  onRenameCommit: (id: string) => void;
  onRenameCancel: () => void;
}

function updatedSec(s: SessionSummary): number {
  return typeof s.updated_at === "number" ? s.updated_at : Date.parse(s.updated_at) / 1000;
}

function BotAvatar({ color }: { color: string }) {
  if (color) return <span className="sidebar-bot-dot" style={{ background: color }} />;
  return (
    <span className="sidebar-bot-glyph" aria-hidden="true">
      <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <rect x="4" y="7.5" width="12" height="9" rx="2" />
        <path d="M8 7.5V5a2 2 0 0 1 4 0v2.5" />
        <circle cx="8" cy="12" r="0.7" fill="currentColor" stroke="none" />
        <circle cx="12" cy="12" r="0.7" fill="currentColor" stroke="none" />
      </svg>
    </span>
  );
}

export default function BotsListPanel({
  refreshKey,
  sessions,
  activeSessionId,
  selectedBotId,
  coordinatorSessionId,
  coordinatorName,
  onSessionSelect,
  onNewChat,
  onOpenBot,
  onNewBot,
  onNewTeam,
  onDeleteBot,
  renamingId,
  renameValue,
  toVaultBusy,
  onContextMenu,
  onMenuBtnClick,
  onTitleDoubleClick,
  onRenameChange,
  onRenameCommit,
  onRenameCancel,
}: Props) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [error, setError] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [botMenu, setBotMenu] = useState<{ bot: Bot; x: number; y: number } | null>(null);
  const [teamsCollapsed, setTeamsCollapsed] = useState(loadTeamsCollapsed);

  useEffect(() => {
    getBots()
      .then((list) => { setBots(list); setError(false); })
      .catch(() => setError(true));
  }, [refreshKey]);

  useEffect(() => {
    if (!botMenu) return;
    const close = () => setBotMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("resize", close);
    };
  }, [botMenu]);

  const sessionsByBot = useMemo(() => {
    const map = new Map<string, SessionSummary[]>();
    const unprojected: SessionSummary[] = [];
    for (const s of sessions) {
      if (s.project_id) {
        const list = map.get(s.project_id) || [];
        list.push(s);
        map.set(s.project_id, list);
      } else {
        unprojected.push(s);
      }
    }
    unprojected.sort((a, b) => updatedSec(b) - updatedSec(a));
    // The coordinator master session owns the top slot of the main row.
    if (coordinatorSessionId) {
      unprojected.sort((a, b) => (a.id === coordinatorSessionId ? -1 : b.id === coordinatorSessionId ? 1 : 0));
    }
    map.set("main", unprojected);
    for (const [pid, list] of map) {
      if (pid === "main") continue;
      list.sort((a, b) => updatedSec(b) - updatedSec(a));
    }
    return map;
  }, [sessions, coordinatorSessionId]);

  // Team chats (group conversations) get their own always-visible section —
  // they also stay nested under their lead bot, but burying them in the
  // accordion made them invisible.
  const teams = useMemo(() => {
    const out: { session: SessionSummary; name: string; members: string[]; mode: string }[] = [];
    for (const s of sessions) {
      if (!s.team) continue;
      try {
        const t = JSON.parse(s.team) as { name?: string; members?: string[]; mode?: string };
        out.push({ session: s, name: t.name || s.title || "Team", members: t.members || [], mode: t.mode || "lead" });
      } catch {
        out.push({ session: s, name: s.title || "Team", members: [], mode: "lead" });
      }
    }
    return out.sort((a, b) => updatedSec(b.session) - updatedSec(a.session));
  }, [sessions]);

  const botsById = useMemo(() => new Map(bots.map((b) => [b.id, b])), [bots]);

  // Rows: coordinator "main" always first (synthesized when the coordinator
  // is disabled so unprojected chats keep a home), then bots by recency.
  // The coordinator's handle derives from its configured name — never the
  // literal "main" (that stays an internal id only).
  const rows = useMemo(() => {
    const fallbackName = coordinatorName || "Main";
    const fallbackHandle = fallbackName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 32) || "main";
    const main = bots.find((b) => b.kind === "coordinator") ?? {
      id: "main",
      kind: "coordinator" as const,
      name: fallbackName,
      description: "",
      persona: "",
      handle: fallbackHandle,
      color: "",
      icon: "",
      session_id: "",
      session_count: sessionsByBot.get("main")?.length ?? 0,
      vault_path: "",
      created_at: 0,
      updated_at: 0,
    };
    const projectBots = bots
      .filter((b) => b.kind === "bot")
      .slice()
      .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
    return [main, ...projectBots];
  }, [bots, coordinatorName, sessionsByBot]);

  const handleClick = (bot: Bot, ownSessions: SessionSummary[]) => {
    // The bot's own chat always wins the primary click — a team session
    // living under this bot's project must never hijack it.
    if (ownSessions.length > 0) {
      onSessionSelect(ownSessions[0].id, bot.kind === "coordinator" ? null : bot.id);
    } else {
      onNewChat(bot.kind === "coordinator" ? null : bot.id);
    }
  };

  return (
    <div className="sidebar-section sidebar-bots-section">
      <div className="sidebar-sessions-header">
        <div className="sidebar-section-label">Bots</div>
      </div>
      {error && <div className="sidebar-error">Couldn&apos;t load bots.</div>}
      <div className="sidebar-bot-list">
        {rows.map((bot) => {
          const botSessions = sessionsByBot.get(bot.id) ?? [];
          // Team/group sessions live under the lead bot's project — the
          // bot's own (non-team) chats drive the primary click.
          const ownSessions = botSessions.filter((s) => !s.team);
          const primaryOwnId = ownSessions.length > 0 ? ownSessions[0].id : null;
          // Nested rows the chevron reveals: everything except the primary
          // own chat the row click opens (side conversations + groups).
          const nestedCount = botSessions.length - (primaryOwnId ? 1 : 0);
          const expanded = expandedId === bot.id;
          return (
            <div key={bot.id} className="sidebar-bot-item">
              <div className="sidebar-bot-item-row">
                {nestedCount > 0 ? (
                  <button
                    className={`sidebar-bot-chevron-btn${expanded ? " sidebar-bot-chevron-btn--open" : ""}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedId((cur) => (cur === bot.id ? null : bot.id));
                    }}
                    aria-expanded={expanded}
                    title={expanded ? "Collapse chats" : "Expand chats"}
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M6 4l4 4-4 4" />
                    </svg>
                  </button>
                ) : (
                  <span className="sidebar-bot-chevron-spacer" />
                )}
                <button
                  className={`sidebar-bot-header${selectedBotId === bot.id ? " sidebar-bot-header--active" : ""}`}
                  onClick={() => handleClick(bot, ownSessions)}
                  onContextMenu={(e) => {
                    if (bot.kind !== "bot") return;
                    e.preventDefault();
                    setBotMenu({ bot, x: e.clientX, y: e.clientY });
                  }}
                  title={bot.kind === "coordinator" ? `${bot.name} — your main chat` : `${bot.name} @${bot.handle}`}
                >
                  <BotAvatar color={bot.color} />
                  <span className="sidebar-bot-names">
                    <span className="sidebar-bot-name">{bot.name}</span>
                    <span className="sidebar-bot-handle">@{bot.handle || bot.id}</span>
                  </span>
                  {botSessions.length > 1 && <span className="sidebar-bot-count">{botSessions.length}</span>}
                </button>
                <button
                  className="sidebar-bot-settings"
                  title="Bot workspace"
                  aria-label={`Open ${bot.name} workspace`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenBot(bot.id);
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="10" cy="10" r="2.6" />
                    <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4" />
                  </svg>
                </button>
              </div>
              {expanded && nestedCount > 0 && (
                <div className="sidebar-bot-sessions">
                  {botSessions.map((s) => (
                    <SessionItem
                      key={s.id}
                      session={s}
                      isActive={s.id === activeSessionId}
                      isRenaming={renamingId === s.id}
                      renameValue={renameValue}
                      toVaultBusy={toVaultBusy}
                      isMaster={s.id === coordinatorSessionId}
                      isTeam={!!s.team}
                      onSelect={() => onSessionSelect(s.id, bot.kind === "coordinator" ? null : bot.id)}
                      onContextMenu={(e) => onContextMenu(e, s.id)}
                      onMenuBtnClick={(e) => onMenuBtnClick(e, s.id)}
                      onTitleDoubleClick={(e) => onTitleDoubleClick(e, s.id, s.title || "")}
                      onRenameChange={onRenameChange}
                      onRenameCommit={() => onRenameCommit(s.id)}
                      onRenameCancel={onRenameCancel}
                      sideConversation={!!primaryOwnId && s.id !== primaryOwnId}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
        <div className="sidebar-bot-new-row">
          <button className="sidebar-bot-new" onClick={onNewBot} title="Create a new bot">
            <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" />
              <line x1="4" y1="10" x2="16" y2="10" />
            </svg>
            New bot
          </button>
          <button className="sidebar-bot-new" onClick={onNewTeam} title="Create a group chat with several bots">
            <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="7" cy="8" r="2.6" />
              <path d="M2.5 16c0-2.5 2-4.2 4.5-4.2s4.5 1.7 4.5 4.2" />
              <path d="M13.2 5.6a2.4 2.4 0 0 1 0 4.8M14.5 11.9c2 .3 3.2 1.8 3.2 4.1" />
            </svg>
            Team chat
          </button>
        </div>
      </div>
      {teams.length > 0 && (
        <div className="sidebar-teams-section">
          <button
            className="sidebar-section-head"
            onClick={() => {
              setTeamsCollapsed((v) => {
                try { localStorage.setItem(TEAMS_COLLAPSED_KEY, v ? "0" : "1"); } catch { /* ignore */ }
                return !v;
              });
            }}
            aria-expanded={!teamsCollapsed}
            title={teamsCollapsed ? "Expand teams" : "Collapse teams"}
          >
            <span className="sidebar-section-label">Times</span>
            <svg
              className={`sidebar-collapse-chevron${teamsCollapsed ? "" : " sidebar-collapse-chevron--open"}`}
              width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M6 4l4 4-4 4" />
            </svg>
          </button>
          {!teamsCollapsed && (
            <div className="sidebar-team-list">
              {teams.map(({ session, name, members, mode }) => (
              <button
                key={session.id}
                className={`sidebar-team-row${activeSessionId === session.id ? " sidebar-team-row--active" : ""}`}
                title={
                  members
                    .map((m) => botsById.get(m))
                    .filter(Boolean)
                    .map((b) => `@${b!.handle}`)
                    .join(" ") || "Group chat"
                }
                onClick={() => onSessionSelect(session.id, session.project_id)}
              >
                <svg width="11" height="11" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="7" cy="8" r="2.6" />
                  <path d="M2.5 16c0-2.5 2-4.2 4.5-4.2s4.5 1.7 4.5 4.2" />
                  <path d="M13.2 5.6a2.4 2.4 0 0 1 0 4.8M14.5 11.9c2 .3 3.2 1.8 3.2 4.1" />
                </svg>
                <span className="sidebar-team-names">
                  <span className="sidebar-team-name">{name}</span>
                  <span className="sidebar-team-members">
                    {members
                      .map((m) => botsById.get(m)?.handle)
                      .filter(Boolean)
                      .map((h) => `@${h}`)
                      .join(" · ") || "—"}
                  </span>
                </span>
                {mode === "round" && <span className="sidebar-team-mode" title="Every member answers in turn">round</span>}
              </button>
              ))}
            </div>
          )}
        </div>
      )}
      {botMenu && (
        <div
          className="sidebar-bot-menu"
          style={{ left: Math.min(botMenu.x, window.innerWidth - 170), top: botMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button onClick={() => { onOpenBot(botMenu.bot.id); setBotMenu(null); }}>
            Bot workspace
          </button>
          <button
            className="sidebar-bot-menu--danger"
            onClick={() => { onDeleteBot(botMenu.bot); setBotMenu(null); }}
          >
            Delete bot…
          </button>
        </div>
      )}
    </div>
  );
}
