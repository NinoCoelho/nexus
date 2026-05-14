import { useCallback, useEffect, useState } from "react";
import { BASE } from "../api/base";
import { subscribeGlobalNotifications } from "../api/chat";

export interface RunningJob {
  id: string;
  type: string;
  label: string;
  session_id: string | null;
  elapsed_seconds?: number;
  extra?: Record<string, unknown>;
}

export function useRunningJobs() {
  const [jobs, setJobs] = useState<RunningJob[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${BASE}/jobs`)
      .then((r) => (r.ok ? r.json() : { jobs: [] }))
      .then((data) => {
        if (!cancelled) setJobs(data.jobs ?? []);
      })
      .catch(() => {});

    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind === "job_started") {
        setJobs((prev) => {
          const existing = prev.find((j) => j.id === event.data.id);
          if (existing) return prev;
          return [...prev, {
            id: event.data.id,
            type: event.data.type,
            label: event.data.label,
            session_id: event.data.session_id ?? null,
            extra: event.data.extra,
          }];
        });
      } else if (event.kind === "job_done") {
        setJobs((prev) => prev.filter((j) => j.id !== event.data.job_id));
      }
    });
    return () => sub.close();
  }, []);

  const killJob = useCallback(async (jobId: string) => {
    try {
      const res = await fetch(`${BASE}/jobs/${jobId}/kill`, { method: "POST" });
      if (res.ok) {
        setJobs((prev) => prev.filter((j) => j.id !== jobId));
      }
    } catch { /* best-effort */ }
  }, []);

  return { jobs, killJob };
}
