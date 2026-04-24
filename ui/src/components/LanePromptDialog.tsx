/**
 * LanePromptDialog — set the auto-dispatch prompt + model for a lane.
 *
 * When a card is dropped into a lane that has a prompt set, the server
 * background-dispatches the agent with that prompt as context. If a model
 * is also set, that model is used; otherwise the agent's default model
 * applies.
 */

import { useEffect, useState } from "react";
import { getRouting, type KanbanLane } from "../api";
import "./Modal.css";

interface Props {
  lane: KanbanLane;
  onCancel: () => void;
  onSubmit: (patch: { prompt: string | null; model: string | null }) => void | Promise<void>;
}

export default function LanePromptDialog({ lane, onCancel, onSubmit }: Props) {
  const [prompt, setPrompt] = useState(lane.prompt ?? "");
  const [model, setModel] = useState(lane.model ?? "");
  const [available, setAvailable] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRouting()
      .then((r) => {
        if (!cancelled) setAvailable(r.available_models ?? []);
      })
      .catch(() => { /* offline: leave list empty, falls back to free-text input */ });
    return () => { cancelled = true; };
  }, []);

  async function handleSave() {
    setSaving(true);
    try {
      await onSubmit({
        prompt: prompt.trim() ? prompt.trim() : null,
        model: model.trim() ? model.trim() : null,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()} style={{ minWidth: 480 }}>
        <div className="modal-title">Lane settings — {lane.title}</div>
        <p className="modal-message">
          When a card is dropped into this lane, the prompt below is auto-dispatched
          as context to the agent. If a model is set, the agent uses it for the run.
        </p>

        <label className="modal-field-label">Auto-dispatch prompt</label>
        <textarea
          className="modal-input"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. Summarise what was accomplished in this card."
          rows={4}
          autoFocus
        />

        <label className="modal-field-label">Model</label>
        {available.length > 0 ? (
          <select
            className="modal-input"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="">— Use default —</option>
            {available.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        ) : (
          <input
            className="modal-input"
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="Model id (leave blank to use default)"
          />
        )}

        <div className="modal-actions">
          <button className="modal-btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn--primary"
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
