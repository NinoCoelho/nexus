import { useCallback, useEffect, useState } from "react";
import { listRunningJobs, killRunningJob, type RunningJob } from "../api/jobs";
import { subscribeGlobalNotifications } from "../api/chat";

export type { RunningJob };

export function useRunningJobs() {
  const [jobs, setJobs] = useState<RunningJob[]>([]);

  useEffect(() => {
    let cancelled = false;
    listRunningJobs()
      .then((list) => {
        if (!cancelled) setJobs(list);
      })
      .catch(() => {});

    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind === "job_started") {
        const startedAt = event.data.started_at ?? Date.now() / 1000;
        setJobs((prev) => {
          const existing = prev.find((j) => j.id === event.data.id);
          if (existing) return prev;
          return [...prev, {
            id: event.data.id,
            type: event.data.type,
            label: event.data.label,
            session_id: event.data.session_id ?? null,
            started_at: startedAt,
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
      if (await killRunningJob(jobId)) {
        setJobs((prev) => prev.filter((j) => j.id !== jobId));
      }
    } catch { /* best-effort */ }
  }, []);

  return { jobs, killJob };
}
