/**
 * useCalendarAlerts — subscribes to the cross-session ``calendar_alert``
 * events and surfaces them as toast notifications. Fire-and-forget; no
 * answer is expected from the user (unlike HITL approvals).
 *
 * Mounted once at the app root so toasts appear regardless of which view
 * is active.
 */

import { useEffect } from "react";
import { subscribeGlobalNotifications, type CalendarAlertPayload } from "../api/chat";
import { useToast } from "../toast/ToastProvider";

interface Options {
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string) => void;
  onOpenCalendar?: (path: string) => void;
}

export function useCalendarAlerts({ onOpenInChat: _ignored, onOpenCalendar }: Options = {}) {
  void _ignored;
  const toast = useToast();

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sessionId, event) => {
      if (event.kind !== "calendar_alert") return;
      const data = event.data as CalendarAlertPayload;
      const when = formatTime(data.start, !!data.all_day);
      const detail = [when, data.calendar_title]
        .filter(Boolean)
        .join(" · ");
      toast.info(data.title, {
        detail: detail || undefined,
        duration: 12000,
        action: onOpenCalendar
          ? {
              label: "Open calendar",
              onClick: () => onOpenCalendar(data.path),
            }
          : undefined,
      });
    });
    return () => sub.close();
  }, [toast, onOpenCalendar]);
}

function formatTime(start: string, allDay: boolean): string {
  if (!start) return "";
  if (allDay) {
    const [y, m, d] = start.split("-").map(Number);
    if (!y) return start;
    const dt = new Date(y, (m ?? 1) - 1, d ?? 1);
    return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const dt = new Date(start);
  if (Number.isNaN(dt.getTime())) return start;
  return dt.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
