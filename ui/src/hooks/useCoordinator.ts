/**
 * useCoordinator — resolves the coordinator (master chat) identity from
 * /config so the UI can badge the master session and later toggle
 * coordinator settings. Refreshes when settings change.
 */

import { useEffect, useState } from "react";
import { getConfig } from "../api";

export interface CoordinatorState {
  enabled: boolean;
  sessionId: string | null;
}

export function useCoordinator(settingsRevision: number): CoordinatorState {
  const [state, setState] = useState<CoordinatorState>({ enabled: false, sessionId: null });

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((cfg) => {
        if (cancelled) return;
        const coord = cfg.coordinator;
        setState({
          enabled: !!coord?.enabled,
          sessionId: coord?.session_id || null,
        });
      })
      .catch(() => {
        if (!cancelled) setState({ enabled: false, sessionId: null });
      });
    return () => {
      cancelled = true;
    };
  }, [settingsRevision]);

  return state;
}
