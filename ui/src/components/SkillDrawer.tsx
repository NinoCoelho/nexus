/**
 * SkillDrawer — slide-out panel that loads, renders, and edits a skill's SKILL.md.
 *
 * Opened from the AgentGraphView (click a skill node) or from the
 * skill list in Settings. The full body is fetched on open via
 * GET /skills/{name}; saving uses PUT /skills/{name}, which runs the
 * same guard scan as the agent's skill_manage tool.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import CodeMirror from "@uiw/react-codemirror";
import { markdown } from "@codemirror/lang-markdown";
import MarkdownView from "./MarkdownView";
import Modal from "./Modal";
import { useVaultLinkPreview } from "./vaultLink";
import { useToast } from "../toast/ToastProvider";
import { deleteSkill, getSkill, updateSkill, type SkillDetail } from "../api";
import "./SkillDrawer.css";

interface Props {
  skillName: string | null;
  onClose: () => void;
  /** Fired after a successful delete so the host can refresh its skill
   *  list / graph. The drawer always closes itself first. */
  onDeleted?: (name: string) => void;
}

export default function SkillDrawer({ skillName, onClose, onDeleted }: Props) {
  const { t } = useTranslation("skillWizard");
  const toast = useToast();
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
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

  async function handleDelete() {
    if (!skillName) return;
    setConfirmDelete(false);
    setDeleting(true);
    try {
      await deleteSkill(skillName);
      toast.success(`Deleted skill "${skillName}"`);
      onDeleted?.(skillName);
      // Broadcast so any open graph / skill-list view refreshes without
      // having to thread a revision counter through App.
      window.dispatchEvent(new CustomEvent("nexus:skills-changed"));
      onClose();
    } catch (e) {
      toast.error("Could not delete skill", {
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setDeleting(false);
    }
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
              <>
                <button className="drawer-close" onClick={() => setEditing(true)} aria-label="Edit">
                  Edit
                </button>
                <button
                  className="drawer-close drawer-close--danger"
                  onClick={() => setConfirmDelete(true)}
                  disabled={deleting}
                  aria-label="Delete"
                >
                  {deleting ? "Deleting…" : "Delete"}
                </button>
              </>
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
            <>
              {detail.derived_from?.wizard_ask && (
                <div className="skill-drawer-provenance">
                  <span className="skill-drawer-provenance-label">
                    {t("provenance.label")}
                  </span>
                  <span className="skill-drawer-provenance-ask">
                    “{detail.derived_from.wizard_ask}”
                  </span>
                  {detail.derived_from.sources.length > 0 && (
                    <span className="skill-drawer-provenance-sources">
                      {t("provenance.viewSourcesShort", {
                        count: detail.derived_from.sources.length,
                      })}
                    </span>
                  )}
                </div>
              )}
              <MarkdownView onVaultLinkPreview={onPreview}>{detail.body}</MarkdownView>
            </>
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
      {confirmDelete && skillName && (
        <Modal
          kind="confirm"
          danger
          title={`Delete "${skillName}"?`}
          message="The skill folder will be removed from disk. This can't be undone from the UI; only re-running discovery (or restoring from an export) brings it back."
          confirmLabel="Delete"
          onCancel={() => setConfirmDelete(false)}
          onSubmit={handleDelete}
        />
      )}
    </>
  );
}
