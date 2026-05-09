import { useCallback, useEffect, useRef, useState } from "react";
import {
  subscribeGlobalNotifications,
  type CalendarAlarmPayload,
} from "../api/chat";
import { ackAlarm, snoozeAlarm } from "../api/calendar";

export interface ActiveAlarm {
  eventId: string;
  title: string;
  body: string;
  start: string;
  calendarTitle: string;
  path: string;
  countdownSeconds: number;
  isOverdue: boolean;
  occurrenceStart: string;
  receivedAt: number;
}

interface Options {
  onOpenCalendar?: (path: string) => void;
}

export function useCalendarAlarms({ onOpenCalendar: _onOpenCalendar }: Options = {}) {
  const [alarms, setAlarms] = useState<ActiveAlarm[]>([]);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const dismiss = useCallback((eventId: string, occurrenceStart: string) => {
    setAlarms((prev) =>
      prev.filter(
        (a) => !(a.eventId === eventId && a.occurrenceStart === occurrenceStart),
      ),
    );
    void ackAlarm(eventId, occurrenceStart).catch(() => {});
  }, []);

  const snooze = useCallback(
    (eventId: string, occurrenceStart: string, minutes: number = 5) => {
      setAlarms((prev) =>
        prev.filter(
          (a) => !(a.eventId === eventId && a.occurrenceStart === occurrenceStart),
        ),
      );
      void snoozeAlarm(eventId, occurrenceStart, minutes).catch(() => {});
    },
    [],
  );

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sessionId, event) => {
      if (event.kind !== "calendar_alarm") return;
      const data = event.data as CalendarAlarmPayload;
      const alarm: ActiveAlarm = {
        eventId: data.event_id,
        title: data.title,
        body: data.body ?? "",
        start: data.start,
        calendarTitle: data.calendar_title ?? "",
        path: data.path,
        countdownSeconds: data.countdown_seconds,
        isOverdue: data.is_overdue,
        occurrenceStart: data.occurrence_start,
        receivedAt: Date.now(),
      };
      setAlarms((prev) => {
        const exists = prev.some(
          (a) =>
            a.eventId === alarm.eventId &&
            a.occurrenceStart === alarm.occurrenceStart,
        );
        if (exists) return prev;
        return [...prev, alarm];
      });
    });
    return () => sub.close();
  }, []);

  useEffect(() => {
    tickRef.current = setInterval(() => {
      setAlarms((prev) =>
        prev.map((a) => {
          const elapsed = Math.floor((Date.now() - a.receivedAt) / 1000);
          const newCountdown = Math.max(0, a.countdownSeconds - elapsed);
          const overdue = newCountdown === 0;
          return { ...a, countdownSeconds: newCountdown, isOverdue: overdue };
        }),
      );
    }, 1000);
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, []);

  return { alarms, dismiss, snooze };
}
