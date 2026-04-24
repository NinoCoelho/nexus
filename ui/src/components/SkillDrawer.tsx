/**
 * SkillDrawer — slide-out panel that loads and renders a skill's SKILL.md.
 *
 * Opened from the AgentGraphView (click a skill node) or from the
 * skill list in Settings. The full body is fetched on open via
 * GET /skills/{name} — this is the UI side of the progressive-disclosure
 * pattern (the agent gets name+description in the system prompt and
 * calls skill_view to load the full body on demand).
 */

import { useEffect, useState } from "react";
import MarkdownView from "./MarkdownView";
import { getSkill, type SkillDetail } from "../api";
import "./SkillDrawer.css";

interface Props {
  skillName: string | null;
  onClose: () => void;
}

export default function SkillDrawer({ skillName, onClose }: Props) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!skillName) return;
    setDetail(null);
    setLoading(true);
    getSkill(skillName)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [skillName]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!skillName) return null;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="skill-drawer">
        <div className="drawer-header">
          <span className="drawer-title">{skillName}</span>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="drawer-body">
          {loading && <p className="drawer-loading">Loading…</p>}
          {!loading && detail && (
            <MarkdownView>{detail.body}</MarkdownView>
          )}
          {!loading && !detail && (
            <p className="drawer-error">Could not load skill.</p>
          )}
        </div>
      </div>
    </>
  );
}
