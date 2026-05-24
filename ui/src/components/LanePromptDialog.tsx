/**
 * LanePromptDialog — set the auto-dispatch prompt + model + webhook for a lane.
 */

import { useEffect, useState } from "react";
import { getRouting, getLaneWebhook, setLaneWebhook, type KanbanLane } from "../api";
import "./Modal.css";

interface Props {
  lane: KanbanLane;
  boardPath: string;
  onCancel: () => void;
  onSubmit: (patch: { prompt: string | null; model: string | null }) => void | Promise<void>;
}

export default function LanePromptDialog({ lane, boardPath, onCancel, onSubmit }: Props) {
  const [prompt, setPrompt] = useState(lane.prompt ?? "");
  const [model, setModel] = useState(lane.model ?? "");
  const [available, setAvailable] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const [webhookOpen, setWebhookOpen] = useState(false);
  const [webhookEnabled, setWebhookEnabled] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState<string | null>(null);
  const [webhookCopied, setWebhookCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRouting()
      .then((r) => {
        if (!cancelled) setAvailable(r.available_models ?? []);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!webhookOpen) return;
    let cancelled = false;
    getLaneWebhook(boardPath, lane.id)
      .then((info) => {
        if (!cancelled) {
          setWebhookEnabled(info.enabled);
          setWebhookUrl(info.url);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [webhookOpen, boardPath, lane.id]);

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

  async function handleToggleWebhook(enabled: boolean) {
    setSaving(true);
    try {
      const info = await setLaneWebhook(boardPath, lane.id, { enabled });
      setWebhookEnabled(info.enabled);
      setWebhookUrl(info.url);
    } catch {
      setWebhookEnabled(false);
      setWebhookUrl(null);
    } finally {
      setSaving(false);
    }
  }

  function handleCopyUrl() {
    if (!webhookUrl) return;
    navigator.clipboard.writeText(webhookUrl).then(() => {
      setWebhookCopied(true);
      setTimeout(() => setWebhookCopied(false), 2000);
    }).catch(() => {});
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()} style={{ minWidth: 480 }}>
        <div className="modal-title">Lane settings — {lane.title}</div>
        <p className="modal-message">
          When a card is dropped into this lane, the prompt below is auto-dispatched
          as context to the agent. If a model is set, the agent uses it for the run.
        </p>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <label className="modal-field-label" style={{ marginBottom: 0 }}>Auto-dispatch prompt</label>
          <button
            type="button"
            className={`kanban-icon-btn${webhookOpen ? " kanban-icon-btn--active" : ""}`}
            title="Webhook settings"
            onClick={() => setWebhookOpen((v) => !v)}
            style={{
              background: "none",
              border: "none",
              color: webhookOpen ? "var(--accent)" : "var(--fg-dim)",
              cursor: "pointer",
              fontSize: 14,
              padding: "2px 4px",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 3H3a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h1l2 3V3z" />
              <path d="M10 13h3a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-1l-2-3v9z" />
            </svg>
          </button>
        </div>
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

        {webhookOpen && (
          <div style={{ marginTop: 12, padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-inset)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <label style={{ fontSize: 12, fontWeight: 600 }}>Webhook</label>
              <button
                type="button"
                onClick={() => void handleToggleWebhook(!webhookEnabled)}
                disabled={saving}
                style={{
                  background: webhookEnabled ? "var(--accent)" : "var(--border)",
                  border: "none",
                  borderRadius: 10,
                  width: 36,
                  height: 20,
                  cursor: "pointer",
                  position: "relative",
                  transition: "background 0.15s",
                  padding: 0,
                }}
              >
                <span style={{
                  position: "absolute",
                  top: 2,
                  left: webhookEnabled ? 18 : 2,
                  width: 16,
                  height: 16,
                  borderRadius: "50%",
                  background: "white",
                  transition: "left 0.15s",
                  display: "block",
                }} />
              </button>
              <span style={{ fontSize: 11, color: "var(--fg-dim)" }}>
                {webhookEnabled ? "Enabled" : "Disabled"}
              </span>
            </div>
            <p style={{ fontSize: 11, color: "var(--fg-dim)", margin: "0 0 8px" }}>
              External services can POST payloads to create cards in this lane.
              The payload is sanitised before processing to prevent prompt injection.
            </p>
            {webhookEnabled && webhookUrl && (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <code style={{
                  flex: 1,
                  fontSize: 11,
                  padding: "4px 8px",
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}>
                  {webhookUrl}
                </code>
                <button
                  type="button"
                  onClick={handleCopyUrl}
                  style={{
                    background: "none",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: "4px 8px",
                    fontSize: 11,
                    cursor: "pointer",
                    color: webhookCopied ? "var(--ok, #4caf80)" : "var(--fg-dim)",
                  }}
                >
                  {webhookCopied ? "Copied" : "Copy"}
                </button>
              </div>
            )}
          </div>
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
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
