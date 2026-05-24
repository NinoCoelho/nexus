/**
 * Shared EventSource handler for the wizard build session.
 *
 * Used by both:
 *  - the wizard's BuildStep (live, full feedback while the modal is open)
 *  - the App-level background tracker (so a closed modal still reports the
 *    final outcome via toast + push)
 *
 * The agent's trace bus emits these `kind`s during a build turn:
 *   `iter`         → new turn iteration (counted)
 *   `tool_call`    → name + args
 *   `tool_result`  → name + preview
 *   `delta`        → assistant text chunks (ignored here)
 *   `reply`        → final assistant text, truncated server-side at 200 chars
 *
 * The terminal signal is `reply` matching ``Built skill "<slug>"`` (success)
 * or ``Failed: <reason>`` (failure). On terminal we close the source.
 */

import { BASE } from "../../api/base";

export type BuildStreamStage =
  | "starting"
  | "reviewing"
  | "synthesizing"
  | "saving";

export interface BuildStreamState {
  stage: BuildStreamStage;
  /** Number of related candidates the agent's spawn_subagents call dispatched.
   *  Zero before the first spawn or when no related candidates exist. */
  subagentsTotal: number;
  /** Number of those subagents whose results have come back to the parent.
   *  spawn_subagents returns all results in a single tool_result, so this
   *  flips from 0 to subagentsTotal in one step rather than streaming. */
  subagentsCompleted: number;
  /** Iteration counter from the agent loop's ``iter`` event. */
  iterations: number;
}

export type BuildStreamTerminal =
  | { kind: "success"; skillName: string }
  | { kind: "failure"; reason: string };

export interface BuildStreamHandle {
  /** Disconnect the SSE — call from a useEffect cleanup. */
  close: () => void;
}

export interface BuildStreamCallbacks {
  onState: (next: BuildStreamState) => void;
  onTerminal: (outcome: BuildStreamTerminal) => void;
}

const INITIAL_STATE: BuildStreamState = {
  stage: "starting",
  subagentsTotal: 0,
  subagentsCompleted: 0,
  iterations: 0,
};

export function subscribeBuildStream(
  sessionId: string,
  callbacks: BuildStreamCallbacks,
): BuildStreamHandle {
  const url = `${BASE}/chat/${encodeURIComponent(sessionId)}/events`;
  const es = new EventSource(url, { withCredentials: true });

  let state: BuildStreamState = { ...INITIAL_STATE };
  let terminated = false;
  // The agent's reply event is truncated to 200 chars server-side, so a
  // verbose model that ends with `Built skill "..."` after 4 sentences of
  // narrative loses the success marker. We mirror the ground-truth signal
  // here: when the `skill_manage(create)` tool result comes back ok, we
  // know what got created — we use that as the success fallback if the
  // reply event itself is unparseable.
  let pendingCreateName: string | null = null;
  let confirmedCreatedName: string | null = null;

  const update = (patch: Partial<BuildStreamState>) => {
    state = { ...state, ...patch };
    callbacks.onState(state);
  };

  const finalize = (outcome: BuildStreamTerminal) => {
    if (terminated) return;
    terminated = true;
    callbacks.onTerminal(outcome);
    es.close();
  };

  es.addEventListener("iter", (ev) => {
    try {
      const d = JSON.parse((ev as MessageEvent).data ?? "{}");
      const n = Number(d?.n ?? 0);
      if (Number.isFinite(n)) update({ iterations: n });
    } catch {
      // ignore
    }
  });

  es.addEventListener("tool_call", (ev) => {
    try {
      const d = JSON.parse((ev as MessageEvent).data ?? "{}");
      const name = String(d?.name ?? "");
      const args = d?.args ?? {};
      if (name === "spawn_subagents") {
        const tasks = Array.isArray(args?.tasks) ? args.tasks : [];
        update({
          stage: "reviewing",
          subagentsTotal: tasks.length,
          subagentsCompleted: 0,
        });
      } else if (name === "skill_manage" && args?.action === "create") {
        update({ stage: "saving" });
        if (typeof args?.name === "string") pendingCreateName = args.name;
      }
    } catch {
      // ignore
    }
  });

  es.addEventListener("tool_result", (ev) => {
    try {
      const d = JSON.parse((ev as MessageEvent).data ?? "{}");
      const name = String(d?.name ?? "");
      if (name === "spawn_subagents") {
        update({
          stage: "synthesizing",
          subagentsCompleted: state.subagentsTotal,
        });
      } else if (name === "skill_manage" && pendingCreateName) {
        // Preview is the truncated stringified result. ``"ok": true``
        // confirms the create landed on disk; pair it with the name we
        // captured at tool_call time.
        const preview = String(d?.preview ?? "");
        if (/"ok"\s*:\s*true/.test(preview) && /created/i.test(preview)) {
          confirmedCreatedName = pendingCreateName;
        }
        pendingCreateName = null;
      }
    } catch {
      // ignore
    }
  });

  es.addEventListener("reply", (ev) => {
    try {
      const d = JSON.parse((ev as MessageEvent).data ?? "{}");
      // Trace bus carries the truncated reply under `text`.
      const text = String(d?.text ?? d?.reply ?? "");
      if (!text) return;

      const built = text.match(/Built skill[:\s]+"?([a-z0-9-]+)"?/i);
      if (built) {
        finalize({ kind: "success", skillName: built[1] });
        return;
      }
      const failed = text.match(/Failed:\s*(.+)/i);
      if (failed) {
        finalize({ kind: "failure", reason: failed[1].trim() });
        return;
      }
      // Reply arrived but doesn't fit either pattern (often: the model
      // narrated for too long and the success marker fell past the
      // server-side 200-char truncation). If we already saw a successful
      // skill_manage create, that's the ground truth — use it.
      if (confirmedCreatedName) {
        finalize({ kind: "success", skillName: confirmedCreatedName });
        return;
      }
      // Truly nothing to anchor on — surface the raw reply so the user
      // sees something actionable instead of an indefinite spinner.
      finalize({ kind: "failure", reason: text.slice(0, 200) });
    } catch {
      // ignore
    }
  });

  es.addEventListener("error", () => {
    // EventSource auto-reconnects on transient drops; only surface a hard
    // failure if the connection has fully closed AND we haven't reached a
    // terminal state yet. If we already confirmed a successful create, the
    // dropped connection doesn't matter — the skill exists.
    if (!terminated && es.readyState === EventSource.CLOSED) {
      if (confirmedCreatedName) {
        finalize({ kind: "success", skillName: confirmedCreatedName });
      } else {
        finalize({ kind: "failure", reason: "lost connection to build session" });
      }
    }
  });

  return {
    close: () => {
      if (!terminated) {
        terminated = true;
        es.close();
      }
    },
  };
}
