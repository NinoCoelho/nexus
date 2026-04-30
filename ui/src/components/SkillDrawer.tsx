/**
 * SkillDrawer — slide-out panel that loads, renders, and edits a skill's SKILL.md.
 *
 * Opened from the AgentGraphView (click a skill node) or from the
 * skill list in Settings. The full body is fetched on open via
 * GET /skills/{name}; saving uses PUT /skills/{name}, which runs the
 * same guard scan as the agent's skill_manage tool.
 */

import { useEffect, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { markdown } from "@codemirror/lang-markdown";
import MarkdownView from "./MarkdownView";
import { useVaultLinkPreview } from "./vaultLink";
import { getSkill, updateSkill, type SkillDetail } from "../api";
import "./SkillDrawer.css";

interface Props {
  skillName: string | null;
  onClose: () => void;
}

export default function SkillDrawer({ skillName, onClose }: Props) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const { onPreview, modal } = useVaultLinkPreview();

  useEffect(() => {
    if (!skillName) return;
    setDetail(null);
    setEditing(false);
    setSaveError(null);
    setLoading(true);
    getSkill(skillName)
      .then((d) => { setDetail(d); setDraft(d.body); })
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [skillName]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !editing) onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, editing]);

  async function handleSave() {
    if (!skillName) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await updateSkill(skillName, draft);
      setDetail(updated);
      setDraft(updated.body);
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function handleCancel() {
    if (detail) setDraft(detail.body);
    setEditing(false);
    setSaveError(null);
  }

  if (!skillName) return null;

  return (
    <>
      <div className="drawer-backdrop" onClick={editing ? undefined : onClose} />
      <div className="skill-drawer">
        <div className="drawer-header">
          <span className="drawer-title">{skillName}</span>
          <div style={{ display: "flex", gap: 6 }}>
            {!loading && detail && !editing && (
              <button className="drawer-close" onClick={() => setEditing(true)} aria-label="Edit">
                Edit
              </button>
            )}
            {editing && (
              <>
                <button className="drawer-close" onClick={handleCancel} disabled={saving}>
                  Cancel
                </button>
                <button className="drawer-close" onClick={handleSave} disabled={saving}>
                  {saving ? "Saving…" : "Save"}
                </button>
              </>
            )}
            <button className="drawer-close" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>
        <div className="drawer-body">
          {loading && <p className="drawer-loading">Loading…</p>}
          {!loading && detail && !editing && (
            <MarkdownView onVaultLinkPreview={onPreview}>{detail.body}</MarkdownView>
          )}
          {!loading && detail && editing && (
            <>
              {saveError && <p className="drawer-error">{saveError}</p>}
              <CodeMirror
                value={draft}
                height="calc(100vh - 160px)"
                extensions={[markdown()]}
                onChange={setDraft}
                theme="dark"
              />
            </>
          )}
          {!loading && !detail && (
            <p className="drawer-error">Could not load skill.</p>
          )}
        </div>
      </div>
      {modal}
    </>
  );
}
