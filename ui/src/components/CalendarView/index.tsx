/**
 * CalendarView — top-level component for the Calendar tab.
 *
 * Owns: calendar selection, view mode (month/week/day), visible window,
 * event modal state. Loads the selected calendar via /vault/calendar/events
 * for the current window so RRULE expansion happens on the server.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type Calendar as CalendarFile,
  type CalendarEvent,
  type CalendarSummary,
  addVaultCalendarEvent,
  createVaultCalendar,
  deleteVaultCalendarEvent,
  fireVaultCalendarEvent,
  getVaultCalendar,
  listVaultCalendars,
  patchVaultCalendar,
  patchVaultCalendarEvent,
  queryVaultCalendarEvents,
} from "../../api/calendar";
import { dispatchFromVault, HIDDEN_SEED_MARKER } from "../../api/dispatch";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import CalendarSettingsModal from "./CalendarSettingsModal";
import EventModal, { type EventDraft } from "./EventModal";
import Modal, { type ModalProps } from "../Modal";
import MonthGrid from "./MonthGrid";
import WeekGrid from "./WeekGrid";
import {
  addDays,
  addMonths,
  formatDayRange,
  formatMonthYear,
  formatWeekRange,
  monthGridWindow,
  toIsoUtc,
  weekWindow,
} from "./dateUtils";
import "./CalendarView.css";

type ViewMode = "month" | "week" | "day";

interface Props {
  selectedPath: string | null;
  onSelectPath: (path: string | null) => void;
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string) => void;
}

export default function CalendarView({ selectedPath, onSelectPath, onOpenInChat }: Props) {
  const [calendars, setCalendars] = useState<CalendarSummary[]>([]);
  const [calendar, setCalendar] = useState<CalendarFile | null>(null);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>("week");
  const [visible, setVisible] = useState(() => new Date());
  const [modal, setModal] = useState<{ kind: "create" | "edit"; event?: CalendarEvent; defaultStart: Date } | null>(null);
  const [promptModal, setPromptModal] = useState<ModalProps | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const window_ = useMemo(() => {
    if (viewMode === "month") return monthGridWindow(visible);
    if (viewMode === "week") return weekWindow(visible, 0, 7);
    return weekWindow(visible, 0, 1);
  }, [viewMode, visible]);

  // Load calendar list on mount and whenever the user creates one.
  const reloadList = useCallback(async () => {
    try {
      const res = await listVaultCalendars();
      setCalendars(res.calendars);
      if (!selectedPath && res.calendars.length > 0) {
        onSelectPath(res.calendars[0].path);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to list calendars");
    }
  }, [selectedPath, onSelectPath]);

  useEffect(() => { void reloadList(); }, [reloadList]);

  // Load the selected calendar and its events for the visible window.
  const reload = useCallback(async () => {
    if (!selectedPath) {
      setCalendar(null);
      setEvents([]);
      return;
    }
    try {
      const cal = await getVaultCalendar(selectedPath);
      setCalendar(cal);
      const q = await queryVaultCalendarEvents({
        calendar_path: selectedPath,
        from: toIsoUtc(window_.from),
        to: toIsoUtc(window_.to),
      });
      setEvents(q.events);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load calendar");
    }
  }, [selectedPath, window_]);

  useEffect(() => { void reload(); }, [reload]);

  // SSE: refresh when the file changes on disk (e.g. heartbeat fired an event).
  useVaultEvents((ev) => {
    if (!selectedPath) return;
    if (ev.path !== selectedPath) return;
    void reload();
  });

  // Header: prev / next / today nav.
  const onPrev = useCallback(() => {
    setVisible((v) =>
      viewMode === "month" ? addMonths(v, -1) : addDays(v, viewMode === "week" ? -7 : -1),
    );
  }, [viewMode]);
  const onNext = useCallback(() => {
    setVisible((v) =>
      viewMode === "month" ? addMonths(v, 1) : addDays(v, viewMode === "week" ? 7 : 1),
    );
  }, [viewMode]);
  const onToday = useCallback(() => setVisible(new Date()), []);

  // Modal handlers.
  const openCreate = useCallback((day: Date) => {
    const defaultStart = new Date(day);
    if (defaultStart.getHours() === 0) defaultStart.setHours(12, 0, 0, 0);
    setModal({ kind: "create", defaultStart });
  }, []);

  const openEdit = useCallback((ev: CalendarEvent) => {
    setModal({ kind: "edit", event: ev, defaultStart: new Date(ev.start) });
  }, []);

  const handleSave = useCallback(async (draft: EventDraft) => {
    if (!selectedPath) return;
    try {
      if (modal?.kind === "create" || !modal?.event) {
        await addVaultCalendarEvent(selectedPath, {
          title: draft.title,
          start: draft.startIso,
          end: draft.endIso ?? undefined,
          body: draft.body,
          trigger: draft.trigger || undefined,
          rrule: draft.rrule || undefined,
          all_day: draft.all_day,
          status: draft.status,
          fire_from: draft.fire_from ?? undefined,
          fire_to: draft.fire_to ?? undefined,
          fire_every_min: draft.fire_every_min ?? undefined,
          model: draft.model ?? undefined,
          assignee: draft.assignee ?? undefined,
        });
      } else {
        await patchVaultCalendarEvent(selectedPath, modal.event.id, {
          title: draft.title,
          body: draft.body,
          start: draft.startIso,
          end: draft.endIso,
          status: draft.status,
          trigger: draft.trigger,
          rrule: draft.rrule || null,
          all_day: draft.all_day,
          fire_from: draft.fire_from,
          fire_to: draft.fire_to,
          fire_every_min: draft.fire_every_min,
          model: draft.model,
          assignee: draft.assignee,
        });
      }
      setModal(null);
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save event");
    }
  }, [selectedPath, modal, reload]);

  const handleDelete = useCallback(async () => {
    if (!selectedPath || !modal?.event) return;
    try {
      await deleteVaultCalendarEvent(selectedPath, modal.event.id);
      setModal(null);
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete event");
    }
  }, [selectedPath, modal, reload]);

  const handleEventOpenChat = useCallback(async (ev: CalendarEvent) => {
    if (!selectedPath || !onOpenInChat) return;
    try {
      const res = await dispatchFromVault({
        path: selectedPath,
        event_id: ev.id,
        mode: "chat-hidden",
      });
      onOpenInChat(
        res.session_id,
        res.seed_message ?? `${HIDDEN_SEED_MARKER}${ev.title}`,
        ev.title,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to open chat");
    }
  }, [selectedPath, onOpenInChat]);

  const handleEventFire = useCallback(async (ev: CalendarEvent) => {
    if (!selectedPath) return;
    try {
      await fireVaultCalendarEvent(selectedPath, ev.id);
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fire event");
    }
  }, [selectedPath, reload]);

  const handleAutoTriggerToggle = useCallback(async () => {
    if (!selectedPath || !calendar) return;
    try {
      await patchVaultCalendar(selectedPath, { auto_trigger: !calendar.auto_trigger });
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update calendar");
    }
  }, [selectedPath, calendar, reload]);

  const handleSettingsSave = useCallback(async (updates: { title: string; prompt: string; timezone: string }) => {
    if (!selectedPath) return;
    try {
      await patchVaultCalendar(selectedPath, updates);
      setSettingsOpen(false);
      void reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update calendar");
    }
  }, [selectedPath, reload]);

  const handleCreateNew = useCallback(() => {
    setPromptModal({
      kind: "prompt",
      title: "New calendar",
      message: "Choose a name for your new calendar.",
      defaultValue: "My Calendar",
      placeholder: "Calendar name",
      confirmLabel: "Create",
      onCancel: () => setPromptModal(null),
      onSubmit: async (name) => {
        setPromptModal(null);
        const trimmed = name.trim();
        if (!trimmed) return;
        const slug = trimmed.replace(/[^\w-]+/g, "_");
        const path = `Calendars/${slug}.md`;
        try {
          await createVaultCalendar(path, { title: trimmed });
          await reloadList();
          onSelectPath(path);
        } catch (e) {
          setError(e instanceof Error ? e.message : "Failed to create calendar");
        }
      },
    });
  }, [reloadList, onSelectPath]);

  const headerLabel =
    viewMode === "month" ? formatMonthYear(visible)
    : viewMode === "week" ? formatWeekRange(visible)
    : formatDayRange(visible);

  return (
    <div className="cal-view">
      <div className="cal-header">
        <select
          value={selectedPath ?? ""}
          onChange={(e) => onSelectPath(e.target.value || null)}
        >
          {calendars.length === 0 && <option value="">(none)</option>}
          {calendars.map((c) => (
            <option key={c.path} value={c.path}>{c.title}</option>
          ))}
        </select>
        <button onClick={handleCreateNew}>+ New</button>

        <div className="cal-header-spacer" />

        <button onClick={onPrev}>◀</button>
        <button onClick={onToday}>Today</button>
        <button onClick={onNext}>▶</button>
        <span className="cal-current-label">{headerLabel}</span>

        <div className="cal-header-spacer" />

        <button className={viewMode === "day" ? "active" : ""} onClick={() => setViewMode("day")}>Day</button>
        <button className={viewMode === "week" ? "active" : ""} onClick={() => setViewMode("week")}>Week</button>
        <button className={viewMode === "month" ? "active" : ""} onClick={() => setViewMode("month")}>Month</button>

        {calendar && (
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={!!calendar.auto_trigger}
              onChange={() => void handleAutoTriggerToggle()}
            />
            Auto-fire
          </label>
        )}
        {calendar && (
          <button title="Calendar settings" onClick={() => setSettingsOpen(true)}>⚙</button>
        )}
      </div>

      {error && (
        <div style={{ padding: "6px 16px", background: "#dc2626", color: "white", fontSize: 13 }}>
          {error}
          <button
            onClick={() => setError(null)}
            style={{ marginLeft: 12, background: "transparent", border: "1px solid white", color: "white", borderRadius: 3, padding: "0 6px", cursor: "pointer" }}
          >×</button>
        </div>
      )}

      <div className="cal-grid">
        {selectedPath ? (
          viewMode === "month" ? (
            <MonthGrid
              visible={visible}
              events={events}
              onCellClick={openCreate}
              onEventClick={openEdit}
              onEventOpenChat={(ev) => void handleEventOpenChat(ev)}
              onEventFire={(ev) => void handleEventFire(ev)}
            />
          ) : (
            <WeekGrid
              start={window_.from}
              days={viewMode === "week" ? 7 : 1}
              events={events}
              onSlotClick={openCreate}
              onEventClick={openEdit}
              onEventOpenChat={(ev) => void handleEventOpenChat(ev)}
              onEventFire={(ev) => void handleEventFire(ev)}
            />
          )
        ) : (
          <div style={{ padding: 24, color: "var(--fg-faint)" }}>
            No calendar selected. Click <strong>+ New</strong> to create one.
          </div>
        )}
      </div>

      {modal && selectedPath && (
        <EventModal
          initial={{
            event: modal.event,
            defaultStart: modal.defaultStart,
            defaultDurationMin: calendar?.default_duration_min ?? 30,
          }}
          onSave={handleSave}
          onDelete={modal.event ? handleDelete : undefined}
          onClose={() => setModal(null)}
          onOpenInChat={
            modal.event && onOpenInChat
              ? () => void handleEventOpenChat(modal.event!)
              : undefined
          }
          onFireNow={
            modal.event ? () => void handleEventFire(modal.event!) : undefined
          }
        />
      )}

      {promptModal && <Modal {...promptModal} />}

      {settingsOpen && calendar && (
        <CalendarSettingsModal
          calendar={calendar}
          onSave={handleSettingsSave}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}
