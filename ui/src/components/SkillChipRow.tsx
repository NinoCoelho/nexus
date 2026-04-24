/**
 * SkillChipRow — row of pulsing skill-name chips.
 *
 * Currently unused (not imported by any other component). Intended to show
 * which skills the agent is actively using during a turn, with a pulsing
 * animation on the active chip.
 */

import { useEffect, useRef, useState } from "react";
import { getSkills, type SkillSummary } from "../api";
import "./SkillChipRow.css";

interface Props {
  pulsing: Set<string>;
  onChipClick: (skillName: string) => void;
}

function TrustIcon({ trust }: { trust: SkillSummary["trust"] }) {
  if (trust === "agent") {
    return (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="10,2 12.4,7.5 18.5,8 14,12 15.7,18 10,15 4.3,18 6,12 1.5,8 7.6,7.5" />
      </svg>
    );
  }
  if (trust === "user") {
    return (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="10" cy="7" r="4" />
        <path d="M3 18c0-3.3 3.1-6 7-6s7 2.7 7 6" />
      </svg>
    );
  }
  // builtin
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 2l7.3 4.2v7.6L10 18l-7.3-4.2V6.2z" />
    </svg>
  );
}

export default function SkillChipRow({ pulsing, onChipClick }: Props) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [newAgentSkills, setNewAgentSkills] = useState<Set<string>>(new Set());
  const prevNamesRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await getSkills();
        if (cancelled) return;
        const incoming = new Set(data.map((s) => s.name));
        const prev = prevNamesRef.current;
        const novel = data.filter((s) => !prev.has(s.name) && s.trust === "agent");
        if (novel.length > 0) {
          const names = new Set(novel.map((s) => s.name));
          setNewAgentSkills(names);
          setTimeout(() => setNewAgentSkills(new Set()), 30000);
        }
        prevNamesRef.current = incoming;
        setSkills(data);
      } catch {
        // backend not up yet
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (skills.length === 0) return null;

  return (
    <div className="chip-row-wrap">
      <div className="chip-row">
        {skills.map((skill) => (
          <button
            key={skill.name}
            className={[
              "skill-chip",
              pulsing.has(skill.name) ? "skill-chip--pulse" : "",
              newAgentSkills.has(skill.name) ? "skill-chip--new" : "",
            ].filter(Boolean).join(" ")}
            onClick={() => onChipClick(skill.name)}
            title={skill.description}
          >
            <span className={`chip-icon chip-icon--${skill.trust}`}>
              <TrustIcon trust={skill.trust} />
            </span>
            <span className="chip-name">{skill.name}</span>
            {newAgentSkills.has(skill.name) && (
              <span className="chip-new-badge">NEW</span>
            )}
          </button>
        ))}
      </div>
      <div className="chip-row-fade" aria-hidden="true" />
    </div>
  );
}
