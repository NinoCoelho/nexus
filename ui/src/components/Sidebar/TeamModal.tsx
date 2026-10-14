/**
 * TeamModal — create or edit a group chat (team) of bots.
 *
 * Create mode: picks a name, members (checkboxes, first = lead) and a mode —
 * "lead" (the lead bot answers unless someone is @mentioned) or "round"
 * (every member answers in turn). Edit mode: patches the session's team.
 */

import { useEffect, useState } from "react";
import {
  createTeam,
  getBots,
  updateTeam,
  type Bot,
  type TeamInfo,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import "./TeamModal.css";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Edit mode: the session whose team is being edited. */
  editSessionId?: string | null;
  editTeam?: TeamInfo | null;
  /** Create mode: opens the new team chat. */
  onCreated?: (sessionId: string, leadProjectId: string | null) => void;
  /** Edit mode: signal the app to refresh session state (new team). */
  onUpdated?: (team: TeamInfo | null) => void;
}

export default function TeamModal({
  open,
  onClose,
  editSessionId = null,
  editTeam = null,
  onCreated,
  onUpdated,
}: Props) {
  const toast = useToast();
  const [bots, setBots] = useState<Bot[]>([]);
  const [name, setName] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [mode, setMode] = useState<TeamInfo["mode"]>("lead");
  const [busy, setBusy] = useState(false);

  const isEdit = !!editSessionId;

  useEffect(() => {
    if (!open) return;
    getBots()
      .then((list) => setBots(list.filter((b) => b.kind === "bot")))
      .catch(() => setBots([]));
    setName(editTeam?.name ?? "");
    setMembers(editTeam?.members ?? []);
    setMode(editTeam?.mode ?? "lead");
  }, [open, editTeam]);

  if (!open) return null;

  const toggleMember = (id: string) => {
    setMembers((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]));
  };

  const moveFirst = (id: string) => {
    setMembers((prev) => [id, ...prev.filter((m) => m !== id)]);
  };

  const handleSave = async () => {
    if (!name.trim() || members.length === 0 || busy) return;
    setBusy(true);
    try {
      if (isEdit && editSessionId) {
        const { team } = await updateTeam(editSessionId, { name: name.trim(), members, mode });
        toast.success("Team updated", { detail: team?.name ?? name.trim() });
        onUpdated?.(team ?? null);
        onClose();
      } else {
        const { session_id, team } = await createTeam({ name: name.trim(), members, mode });
        toast.success("Team created", { detail: team.name });
        onClose();
        onCreated?.(session_id, team.members[0] ?? null);
      }
    } catch (e) {
      toast.error(isEdit ? "Couldn't update the team" : "Couldn't create the team", {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  const byId = new Map(bots.map((b) => [b.id, b]));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-dialog team-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{isEdit ? "Edit team" : "New team chat"}</div>
        <div className="modal-message">
          A team is a group chat: @mention any member to address them directly.
          The first member is the lead — it answers messages that mention no one.
        </div>
        <label className="team-field">
          <span>Team name</span>
          <input
            autoFocus
            value={name}
            placeholder="e.g. Content squad"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void handleSave(); }}
          />
        </label>
        <div className="team-field">
          <span>Members ({members.length}{members.length ? ` — lead: ${byId.get(members[0])?.name ?? "?"}` : ""})</span>
          <div className="team-members">
            {bots.length === 0 && <div className="team-members-empty">No bots yet — create one first.</div>}
            {bots.map((b) => {
              const checked = members.includes(b.id);
              const isLead = checked && members[0] === b.id;
              return (
                <label key={b.id} className={`team-member${checked ? " team-member--on" : ""}`}>
                  <input type="checkbox" checked={checked} onChange={() => toggleMember(b.id)} />
                  <span className="team-member-dot" style={{ background: b.color || "var(--accent)" }} />
                  <span className="team-member-name">{b.name}</span>
                  <span className="team-member-handle">@{b.handle}</span>
                  {isLead ? (
                    <span className="team-member-lead">lead</span>
                  ) : checked ? (
                    <button type="button" className="team-member-makelead" onClick={() => moveFirst(b.id)}>
                      make lead
                    </button>
                  ) : null}
                </label>
              );
            })}
          </div>
        </div>
        <div className="team-field">
          <span>Replies</span>
          <div className="team-modes">
            <label className={`team-mode${mode === "lead" ? " team-mode--on" : ""}`}>
              <input type="radio" name="team-mode" checked={mode === "lead"} onChange={() => setMode("lead")} />
              <span>
                <strong>Lead answers</strong>
                The lead replies unless you @mention another member.
              </span>
            </label>
            <label className={`team-mode${mode === "round" ? " team-mode--on" : ""}`}>
              <input type="radio" name="team-mode" checked={mode === "round"} onChange={() => setMode("round")} />
              <span>
                <strong>Everyone answers</strong>
                Each member replies in turn — a round table.
              </span>
            </label>
          </div>
        </div>
        <div className="team-actions">
          <button className="team-cancel" onClick={onClose}>Cancel</button>
          <button
            className="team-save"
            disabled={!name.trim() || members.length === 0 || busy}
            onClick={() => void handleSave()}
          >
            {busy ? "Saving…" : isEdit ? "Save" : "Create team"}
          </button>
        </div>
      </div>
    </div>
  );
}
