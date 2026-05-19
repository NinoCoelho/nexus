import { useCallback, useEffect, useState } from "react";
import { cancelDownload, listDownloads, type DownloadTask } from "../api/localLlm";

export function useActiveDownloads() {
  const [downloads, setDownloads] = useState<DownloadTask[]>([]);

  const refresh = useCallback(async () => {
    try {
      const all = await listDownloads();
      setDownloads(all);
    } catch { /* stale data is fine */ }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const hasActive = downloads.some(
    (d) => d.status === "downloading" || d.status === "pending",
  );

  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(refresh, 1000);
    return () => clearInterval(id);
  }, [hasActive, refresh]);

  const cancel = useCallback(async (taskId: string) => {
    try {
      await cancelDownload(taskId);
      setDownloads((prev) =>
        prev.map((d) =>
          d.task_id === taskId ? { ...d, status: "cancelled" as const } : d,
        ),
      );
    } catch { /* best-effort */ }
  }, []);

  const activeDownloads = downloads.filter(
    (d) => d.status === "downloading" || d.status === "pending",
  );

  return { downloads: activeDownloads, cancel, refresh };
}
