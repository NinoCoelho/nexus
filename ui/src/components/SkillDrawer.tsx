import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
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
            <ReactMarkdown>{detail.body}</ReactMarkdown>
          )}
          {!loading && !detail && (
            <p className="drawer-error">Could not load skill.</p>
          )}
        </div>
      </div>
    </>
  );
}
