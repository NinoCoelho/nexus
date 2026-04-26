/**
 * Modal for creating or editing a calendar event. Used by both Month and
 * Week grids. Emits `onSave` with a partial event payload; the parent
 * decides whether to POST (create) or PATCH (update) based on whether
 * `eventId` was provided.
 */

import { useEffect, useState } from "react";
import type { CalendarEvent, EventStatus, EventTrigger } from "../../api/calendar";
import RepeatPicker from "./RepeatPicker";
import { fromLocalInputValue, toLocalInputValue } from "./dateUtils";

export interface EventDraft {
  title: string;
  body: string;
  startIso: string;
  endIso: string | null;
  status: EventStatus;
  trigger: EventTrigger | "";
  rrule: string;
  all_day: boolean;
  prompt: string;
  fire_from: string | null;
  fire_to: string | null;
  fire_every_min: number | null;
}

interface Props {
  initial: { event?: CalendarEvent; defaultStart: Date; defaultDurationMin: number };
  onSave: (draft: EventDraft) => void;
  onDelete?: () => void;
  onClose: () => void;
  onOpenInChat?: () => void;
  onFireNow?: () => void;
}

const STATUS_OPTIONS: EventStatus[] = [
  "scheduled", "triggered", "done", "failed", "missed", "cancelled",
];

export default function EventModal({ initial, onSave, onDelete, onClose, onOpenInChat, onFireNow }: Props) {
  const ev = initial.event;
  const [title, setTitle] = useState(ev?.title ?? "");
  const [body, setBody] = useState(ev?.body ?? "");
  const startDate = ev?.start ? new Date(ev.start) : initial.defaultStart;
  const endDate = ev?.end
    ? new Date(ev.end)
    : new Date(startDate.getTime() + initial.defaultDurationMin * 60_000);
  const [startLocal, setStartLocal] = useState(toLocalInputValue(startDate));
  const [endLocal, setEndLocal] = useState(toLocalInputValue(endDate));
  const [hasEnd, setHasEnd] = useState(!!ev?.end || !ev);
  const [status, setStatus] = useState<EventStatus>(ev?.status ?? "scheduled");
  const [trigger, setTrigger] = useState<EventTrigger | "">((ev?.trigger as EventTrigger) ?? "");
  const [rrule, setRrule] = useState(ev?.rrule ?? "");
  const [allDay, setAllDay] = useState(!!ev?.all_day);
  const [prompt, setPrompt] = useState(ev?.prompt ?? "");
  const [fireFrom, setFireFrom] = useState(ev?.fire_from ?? "");
  const [fireTo, setFireTo] = useState(ev?.fire_to ?? "");
  const [fireEvery, setFireEvery] = useState<string>(
    ev?.fire_every_min ? String(ev.fire_every_min) : "",
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function handleSave() {
    if (!title.trim()) return;
    let startIso: string;
    let endIso: string | null;
    if (allDay) {
      startIso = startLocal.slice(0, 10);
      endIso = hasEnd ? endLocal.slice(0, 10) : null;
    } else {
      startIso = fromLocalInputValue(startLocal);
      endIso = hasEnd ? fromLocalInputValue(endLocal) : null;
    }
    const fireEveryNum = fireEvery.trim() ? Math.max(1, Math.min(1440, parseInt(fireEvery, 10))) : NaN;
    onSave({
      title: title.trim(),
      body,
      startIso,
      endIso,
      status,
      trigger,
      rrule: rrule.trim(),
      all_day: allDay,
      prompt: prompt.trim(),
      fire_from: allDay && fireFrom ? fireFrom : null,
      fire_to: allDay && fireTo ? fireTo : null,
      fire_every_min: allDay && !Number.isNaN(fireEveryNum) ? fireEveryNum : null,
    });
  }

  return (
    <div className="cal-modal-backdrop" onClick={onClose}>
      <div className="cal-modal cal-modal--form" onClick={(e) => e.stopPropagation()}>
        <h3>{ev ? "Edit event" : "New event"}</h3>

        <input
          className="cal-modal-title-input"
          type="text"
          placeholder="Title"
          value={title}
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
        />

        <div className="cal-modal-grid">
          <span className="cal-modal-grid-label">All Day</span>
          <span>
            <input
              type="checkbox"
              checked={allDay}
              onChange={(e) => setAllDay(e.target.checked)}
            />
          </span>

          <span className="cal-modal-grid-label">Starts</span>
          <input
            type={allDay ? "date" : "datetime-local"}
            value={allDay ? startLocal.slice(0, 10) : startLocal}
            onChange={(e) => setStartLocal(e.target.value)}
          />

          <span className="cal-modal-grid-label">Ends</span>
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type={allDay ? "date" : "datetime-local"}
              value={allDay ? endLocal.slice(0, 10) : endLocal}
              disabled={!hasEnd}
              onChange={(e) => setEndLocal(e.target.value)}
              style={{ flex: 1 }}
            />
            <label style={{ flexDirection: "row", alignItems: "center", gap: 4, fontSize: 11 }}>
              <input
                type="checkbox"
                checked={hasEnd}
                onChange={(e) => setHasEnd(e.target.checked)}
              />
              has end
            </label>
          </span>

          <span className="cal-modal-grid-label">Repeat</span>
          <RepeatPicker value={rrule} onChange={setRrule} />

          <span className="cal-modal-grid-label">Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as EventStatus)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>

          <span className="cal-modal-grid-label">Auto-fire</span>
          <select
            value={trigger}
            onChange={(e) => setTrigger(e.target.value as EventTrigger | "")}
          >
            <option value="">Inherit calendar</option>
            <option value="on_start">On start</option>
            <option value="off">Off</option>
          </select>
        </div>

        {allDay && (
          <fieldset className="cal-modal-fieldset">
            <legend>Auto-fire window</legend>
            <p className="cal-modal-hint">
              For automated rotines (e.g. "check news every 30 min during business hours").
              Leave blank for a record-only event.
            </p>
            <div className="cal-modal-grid">
              <span className="cal-modal-grid-label">From</span>
              <input
                type="time"
                value={fireFrom}
                onChange={(e) => setFireFrom(e.target.value)}
              />
              <span className="cal-modal-grid-label">To</span>
              <input
                type="time"
                value={fireTo}
                onChange={(e) => setFireTo(e.target.value)}
              />
              <span className="cal-modal-grid-label">Every</span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="number"
                  min={1}
                  max={1440}
                  value={fireEvery}
                  onChange={(e) => setFireEvery(e.target.value)}
                  placeholder="30"
                  style={{ width: 80 }}
                />
                <span style={{ fontSize: 12, color: "var(--fg-faint)" }}>min</span>
              </span>
            </div>
          </fieldset>
        )}

        <label className="cal-modal-notes">
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            Agent prompt
            {prompt.trim() && <span title="Agent will run at fire time" style={{ color: "var(--accent, #4d7cff)" }}>⚡</span>}
          </span>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Leave blank to just notify. Add a prompt to run the agent at fire time."
            style={{ minHeight: 56 }}
          />
        </label>

        <label className="cal-modal-notes">
          Notes
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Agenda, attendees, links…"
          />
        </label>

        <div className="cal-modal-actions">
          {onDelete && ev && (
            <button className="danger" onClick={onDelete}>Delete</button>
          )}
          {onFireNow && ev && (status === "missed" || status === "scheduled") && (
            <button onClick={onFireNow}>Fire now</button>
          )}
          {onOpenInChat && ev && (
            <button onClick={onOpenInChat}>Open in chat</button>
          )}
          <div className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={handleSave}>Save</button>
        </div>
      </div>
    </div>
  );
}
