import { useEffect, useRef, useState } from "react";
import {
  HIDDEN_SEED_MARKER,
  getSession,
  subscribeSessionEvents,
  type KanbanCardStatus,
} from "../api";
import ActivityTimeline from "./ActivityTimeline";
import type { TimelineStep } from "./ChatView";
import MarkdownView from "./MarkdownView";
import { useVaultLinkPreview } from "./vaultLink";
import "./CardActivityModal.css";
import "./Modal.css";

interface Props {
  sessionId: string;
  cardTitle: string;
  status?: KanbanCardStatus;
  onClose: () => void;
}

interface LiveState {
  timeline: TimelineStep[];
  reply: string;
  iter: number;
}

/** Read-only window into a background-running (or finished) agent turn for a kanban card. */
export default function CardActivityModal({ sessionId, cardTitle, status, onClose }: Props) {
  const [live, setLive] = useState<LiveState>({ timeline: [], reply: "", iter: 0 });
  const [loading, setLoading] = useState(true);
  const stepCounter = useRef(0);
  const streaming = status === "running";
  const { onPreview, modal } = useVaultLinkPreview();

  // Seed from persisted history.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const detail = await getSession(sessionId);
        if (cancelled) return;
        const timeline: TimelineStep[] = [];
        let lastReply = "";
        const raw = detail.messages;
        for (let i = 0; i < raw.length; i++) {
          const m = raw[i];
          if (m.role === "assistant") {
            const content = m.content ?? "";
            const isPartial = /^\[(interrupted|cancelled|iteration_limit|empty_response|llm_error|crashed|background_interrupted)\]/.test(content);
            const toolCalls = Array.isArray(m.tool_calls)
              ? (m.tool_calls as Array<{ id?: string; name?: string; arguments?: unknown }>)
              : [];
            const resultsById = new Map<string, string>();
            const resultsByPos: string[] = [];
            let j = i + 1;
            while (j < raw.length && raw[j].role === "tool") {
              const preview = (raw[j].content ?? "").slice(0, 200);
              const tid = raw[j].tool_call_id ?? "";
              if (tid) resultsById.set(tid, preview);
              resultsByPos.push(preview);
              j++;
            }
            if (content.length > 0) {
              timeline.push({ id: `h-t-${stepCounter.current++}`, type: "text", text: content });
              lastReply = content;
            }
            toolCalls.forEach((tc, idx) => {
              const name = tc.name ?? "";
              if (!name) return;
              const preview = resultsById.get(tc.id ?? "") ?? resultsByPos[idx];
              timeline.push({
                id: `h-c-${stepCounter.current++}`,
                type: "tool",
                tool: name,
                args: tc.arguments,
                result_preview: preview,
                status: preview != null ? "done" : isPartial ? "pending" : "done",
              });
            });
            i = j - 1;
          }
        }
        setLive({ timeline, reply: lastReply, iter: 0 });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Live event stream (only active while running).
  useEffect(() => {
    if (!streaming) return;
    const es = subscribeSessionEvents(sessionId, (event) => {
      if (event.kind === "iter") {
        setLive((prev) => ({ ...prev, iter: event.data.n }));
        return;
      }
      if (event.kind === "tool_call") {
        const { name, args } = event.data;
        setLive((prev) => ({
          ...prev,
          timeline: [
            ...prev.timeline,
            {
              id: `l-c-${stepCounter.current++}`,
              type: "tool",
              tool: name,
              args,
              status: "pending",
            },
          ],
        }));
        return;
      }
      if (event.kind === "tool_result") {
        const { name, preview } = event.data;
        setLive((prev) => {
          const next = [...prev.timeline];
          for (let i = next.length - 1; i >= 0; i--) {
            const s = next[i];
            if (s.type === "tool" && s.tool === name && s.status === "pending") {
              next[i] = { ...s, status: "done", result_preview: preview };
              break;
            }
          }
          return { ...prev, timeline: next };
        });
        return;
      }
      if (event.kind === "delta") {
        // Stream assistant text into the same trailing live-text bubble
        // we settle on `reply`. Token-level updates so the modal feels
        // alive while the agent is composing.
        const chunk = event.data.text ?? "";
        if (!chunk) return;
        setLive((prev) => {
          const accum = prev.reply + chunk;
          const tail = prev.timeline[prev.timeline.length - 1];
          if (tail && tail.type === "text" && tail.id.startsWith("l-t-")) {
            const next = prev.timeline.slice(0, -1);
            next.push({ ...tail, text: accum });
            return { ...prev, reply: accum, timeline: next };
          }
          return {
            ...prev,
            reply: accum,
            timeline: [
              ...prev.timeline,
              { id: `l-t-${stepCounter.current++}`, type: "text", text: accum },
            ],
          };
        });
        return;
      }
      if (event.kind === "reply") {
        const text = event.data.text ?? "";
        setLive((prev) => ({
          ...prev,
          reply: text,
          timeline: [
            ...prev.timeline.filter((s) => s.type !== "text" || !s.id.startsWith("l-t-")),
            { id: `l-t-${stepCounter.current++}`, type: "text", text },
          ],
        }));
      }
    });
    return () => {
      es.close();
    };
  }, [sessionId, streaming]);

  const statusLabel =
    status === "running"
      ? "Running"
      : status === "done"
      ? "Done"
      : status === "failed"
      ? "Failed"
      : "";

  const displayReply = live.reply.replace(new RegExp(`^${HIDDEN_SEED_MARKER.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`), "");

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog card-activity-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-activity-header">
          <div className="card-activity-title">
            <span className="card-activity-card">{cardTitle}</span>
            {statusLabel && (
              <span className={`card-activity-status card-activity-status--${status}`}>
                {streaming && <span className="card-activity-spinner" />}
                {statusLabel}
              </span>
            )}
          </div>
          <button className="modal-btn" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="card-activity-body">
          {loading && live.timeline.length === 0 ? (
            <div className="card-activity-empty">Loading…</div>
          ) : live.timeline.length === 0 ? (
            <div className="card-activity-empty">
              No activity yet. The agent is queuing…
            </div>
          ) : (
            <>
              <div className="card-activity-timeline-wrap">
                <ActivityTimeline
                  steps={live.timeline}
                  streaming={streaming}
                />
              </div>
              {displayReply.trim() && (
                <div className="card-activity-reply">
                  <MarkdownView onVaultLinkPreview={onPreview} linkifyVaultPaths>
                    {displayReply}
                  </MarkdownView>
                </div>
              )}
            </>
          )}
        </div>
      </div>
      {modal}
    </div>
  );
}
