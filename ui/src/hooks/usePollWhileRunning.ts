import { useEffect, useRef } from "react";

export function usePollWhileRunning(
  isRunning: boolean,
  loadFn: () => void,
  intervalMs: number = 1500,
) {
  const prevRunning = useRef(false);

  useEffect(() => {
    if (isRunning && !prevRunning.current) {
      loadFn();
    }
    prevRunning.current = isRunning;
  }, [isRunning, loadFn]);

  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(loadFn, intervalMs);
    return () => clearInterval(id);
  }, [isRunning, loadFn, intervalMs]);
}
