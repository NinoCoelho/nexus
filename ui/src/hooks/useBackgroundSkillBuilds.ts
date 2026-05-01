/**
 * Tracks wizard-initiated skill builds that the user has dismissed so they
 * keep running on the server (the build is an ``asyncio.create_task`` —
 * closing the modal only disconnects the modal's SSE; the server-side turn
 * keeps going). When a tracked build finishes, surfaces the outcome via
 * a toast with a "Try it now" / "Show me" affordance.
 *
 * State is kept in localStorage so a page refresh doesn't drop in-flight
 * builds — the hook re-attaches an SSE for each persisted entry on mount.
 */

import { useEffect, useRef } from "react";
import type { TFunction } from "i18next";
import type { ToastAPI } from "../toast/ToastProvider";
import {
  subscribeBuildStream,
  type BuildStreamHandle,
} from "../components/SkillWizard/buildStream";

const STORAGE_KEY = "nexus.skillWizard.backgroundBuilds";
const CHANGE_EVENT = "nexus:bg-builds:changed";

export interface BackgroundBuild {
  sessionId: string;
  candidateTitle: string;
  startedAt: number;
}

function loadPending(): BackgroundBuild[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function savePending(arr: BackgroundBuild[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(arr));
  } catch {
    // Quota / private mode — drop silently; the worst case is we lose
    // the resume-after-refresh property for this build.
  }
}

/**
 * Add a build to the background tracking list. Idempotent on session id.
 * Called from the wizard when the user clicks "Run in background" or closes
 * the modal mid-build.
 */
export function trackBackgroundBuild(entry: BackgroundBuild): void {
  const list = loadPending().filter((x) => x.sessionId !== entry.sessionId);
  list.push(entry);
  savePending(list);
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/** Drop a build from tracking without firing toast. Used from the hook
 *  when a terminal event arrives, and from manual user-dismiss paths. */
function untrack(sessionId: string): void {
  const list = loadPending().filter((x) => x.sessionId !== sessionId);
  savePending(list);
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

interface UseBackgroundSkillBuildsArgs {
  toast: ToastAPI;
  t: TFunction;
  /** Optional callback fired when a tracked build completes successfully —
   *  e.g. to trigger an agent-graph refresh in App.tsx. */
  onSkillBuilt?: (skillName: string) => void;
  /** Optional callback fired when the user clicks the toast's "Try it now"
   *  action, with the new skill name as argument. App.tsx can route to a
   *  fresh chat session with the skill activated. */
  onTryItNow?: (skillName: string) => void;
}

/**
 * Mount-once hook that owns SSE subscriptions for every background build.
 *
 * Call this exactly once at the App level. It re-syncs whenever
 * ``trackBackgroundBuild`` fires the change event (same tab) or the
 * ``storage`` event fires (another tab).
 */
export function useBackgroundSkillBuilds({
  toast,
  t,
  onSkillBuilt,
  onTryItNow,
}: UseBackgroundSkillBuildsArgs): void {
  // Map sessionId → SSE handle so we can disconnect when the user
  // drops the entry or the build terminates.
  const handlesRef = useRef<Map<string, BuildStreamHandle>>(new Map());

  useEffect(() => {
    const handles = handlesRef.current;

    const sync = () => {
      const pending = loadPending();
      const wanted = new Set(pending.map((p) => p.sessionId));

      // Disconnect handles for entries that disappeared.
      for (const [sid, h] of handles.entries()) {
        if (!wanted.has(sid)) {
          h.close();
          handles.delete(sid);
        }
      }

      // Open handles for new entries.
      for (const entry of pending) {
        if (handles.has(entry.sessionId)) continue;

        const handle = subscribeBuildStream(entry.sessionId, {
          onState: () => {
            // Background tracker doesn't render in-progress UI — only the
            // terminal toast. Per-stage updates are silenced here.
          },
          onTerminal: (outcome) => {
            untrack(entry.sessionId);
            handles.delete(entry.sessionId);
            if (outcome.kind === "success") {
              const skillName = outcome.skillName;
              onSkillBuilt?.(skillName);
              toast.success(t("skillWizard:bg.success", { skillName }), {
                detail: t("skillWizard:bg.successDetail", {
                  title: entry.candidateTitle,
                }),
                action: onTryItNow
                  ? {
                      label: t("skillWizard:bg.tryItNow"),
                      onClick: () => onTryItNow(skillName),
                    }
                  : undefined,
              });
            } else {
              toast.error(t("skillWizard:bg.failed"), {
                detail: outcome.reason,
              });
            }
          },
        });
        handles.set(entry.sessionId, handle);
      }
    };

    sync();

    const onChange = () => sync();
    const onStorage = (ev: StorageEvent) => {
      if (ev.key === STORAGE_KEY) sync();
    };

    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener("storage", onStorage);
      for (const h of handles.values()) h.close();
      handles.clear();
    };
    // toast/t/onSkillBuilt/onTryItNow are stable enough across renders that
    // closing/reopening every SSE on each render isn't worth the churn —
    // only the underlying entry list change should retrigger sync().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
