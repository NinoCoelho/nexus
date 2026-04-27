/**
 * Modal for editing calendar-level metadata: title, timezone, and the
 * `calendar_prompt` that gets prefixed onto every event the agent runs.
 */

import { useEffect, useState } from "react";
import type { Calendar as CalendarFile } from "../../api/calendar";

interface Props {
  calendar: CalendarFile;
  onSave: (updates: { title: string; prompt: string; timezone: string }) => void;
  onClose: () => void;
}

export default function CalendarSettingsModal({ calendar, onSave, onClose }: Props) {
  const [title, setTitle] = useState(calendar.title ?? "");
  const [prompt, setPrompt] = useState(calendar.calendar_prompt ?? "");
  const [timezone, setTimezone] = useState(calendar.timezone ?? "UTC");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function handleSave() {
    if (!title.trim()) return;
    onSave({ title: title.trim(), prompt, timezone: timezone.trim() || "UTC" });
  }

  return (
    <div className="cal-modal-backdrop" onClick={onClose}>
      <div className="cal-modal cal-modal--form" onClick={(e) => e.stopPropagation()}>
        <h3>Calendar settings</h3>

        <input
          className="cal-modal-title-input"
          type="text"
          placeholder="Title"
          value={title}
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
        />

        <div className="cal-modal-grid">
          <span className="cal-modal-grid-label">Timezone</span>
          <input
            type="text"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            placeholder="America/Sao_Paulo"
          />
        </div>

        <label className="cal-modal-notes">
          Calendar prompt
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Optional. Prefixed onto every agent run for events in this calendar — sets the default tone or rules."
            style={{ minHeight: 120 }}
          />
        </label>

        <div className="cal-modal-actions">
          <div className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={handleSave}>Save</button>
        </div>
      </div>
    </div>
  );
}
