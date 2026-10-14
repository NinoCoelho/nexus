/**
 * TeamChips — group-chat identity strip for the chat topbar: team name,
 * @handle per member (lead first), reply mode, and an edit button.
 */

import { useEffect, useState } from "react";
import { getBots, type Bot, type TeamInfo } from "../api";
import "./TeamChips.css";

interface Props {
  team: TeamInfo;
  onEdit?: () => void;
}

export default function TeamChips({ team, onEdit }: Props) {
  const [bots, setBots] = useState<Record<string, Bot>>({});
  const memberKey = team.members.join(",");

  useEffect(() => {
    let cancelled = false;
    getBots()
      .then((list) => {
        if (cancelled) return;
        const map: Record<string, Bot> = {};
        for (const b of list) map[b.id] = b;
        setBots(map);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [memberKey]);

  return (
    <div className="team-topbar">
      <span className="team-topbar-name" title="Group chat">{team.name}</span>
      {team.members.map((id, i) => {
        const b = bots[id];
        if (!b) return null;
        return (
          <span key={id} className="team-topbar-member" title={i === 0 ? "Lead — answers when nobody is @mentioned" : b.name}>
            {i === 0 && <span className="team-topbar-lead-dot" style={{ background: b.color || "var(--accent)" }} />}
            @{b.handle}
          </span>
        );
      })}
      <span className="team-topbar-mode">{team.mode === "round" ? "everyone answers" : "lead answers"}</span>
      {onEdit && (
        <button className="team-topbar-edit" onClick={onEdit} title="Edit team">
          Edit
        </button>
      )}
    </div>
  );
}
