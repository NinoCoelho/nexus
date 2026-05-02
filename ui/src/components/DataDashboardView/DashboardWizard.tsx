/**
 * DashboardWizard — guided design flow for a new widget or operation.
 *
 * The wizard is a contained chat surface backed by a hidden agent session
 * (kicked via /vault/dashboard/wizard/start). The agent is instructed to
 * ask at most one clarifying question per turn (max 2 total) and then emit
 * a fenced JSON proposal (`nexus-widget-proposal` / `nexus-operation-proposal`)
 * that this component parses and renders as a preview card with
 * Approve / Refine controls.
 *
 * Approve calls the corresponding addWidget / addOperation API; the parent
 * handles the toast + dashboard reload.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { chatStream } from "../../api/chat";
import {
  startWizard,
  type DashboardWidget,
  type DashboardOperation,
  type WidgetKind,
  type WidgetRefresh,
  type WidgetSize,
  type OperationKind,
  type WizardKind,
} from "../../api/dashboard";
import { getSession } from "../../api/sessions";
import MarkdownView from "../MarkdownView";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  folder: string;
  kind: WizardKind;
  /** Called when the user approves the wizard's proposal — parent commits
   *  via addWidget / addOperation, shows toast, closes the modal. */
  onApproveWidget?: (widget: DashboardWidget) => void | Promise<void>;
  onApproveOperation?: (op: DashboardOperation) => void | Promise<void>;
  onCancel: () => void;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** True while the assistant message is still streaming. */
  streaming?: boolean;
}

const PROPOSAL_FENCE: Record<WizardKind, string> = {
  widget: "nexus-widget-proposal",
  operation: "nexus-operation-proposal",
};

const PLACEHOLDER: Record<WizardKind, string> = {
  widget: "What do you want to see on the dashboard?\nE.g. \"Patient visits per quarter as a bar chart\"",
  operation: "What action do you want to add?\nE.g. \"A button that summarizes the last patient seen\"",
};

const TITLE: Record<WizardKind, string> = {
  widget: "✨ Widget wizard",
  operation: "✨ Operation wizard",
};

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

/** Extract the most recent fenced JSON proposal from an assistant message.
 *  Returns parsed JSON or `null`. Also strips the fence from the displayed
 *  text (caller passes ``stripFence: true`` if they want to hide the raw
 *  block while showing a styled proposal card). */
function extractProposal(text: string, fenceTag: string): { json: unknown; stripped: string } | null {
  const re = new RegExp("```" + fenceTag + "\\s*\\n([\\s\\S]*?)```", "g");
  let lastMatch: RegExpExecArray | null = null;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) lastMatch = m;
  if (!lastMatch) return null;
  try {
    const json = JSON.parse(lastMatch[1].trim());
    const stripped = text.replace(re, "").trim();
    return { json, stripped };
  } catch {
    return null;
  }
}

/** Coerce the agent's proposal JSON into a DashboardWidget. Defensive about
 *  missing fields so a well-meaning but underspecified proposal still
 *  commits with sensible defaults. */
function coerceWidget(raw: unknown): DashboardWidget | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const title = typeof r.title === "string" ? r.title.trim() : "";
  const prompt = typeof r.prompt === "string" ? r.prompt.trim() : "";
  if (!title || !prompt) return null;
  const kindRaw = String(r.kind ?? "report");
  const kind: WidgetKind = (["chart", "report", "kpi"] as WidgetKind[]).includes(kindRaw as WidgetKind)
    ? (kindRaw as WidgetKind)
    : "report";
  const refreshRaw = String(r.refresh ?? "daily");
  const refresh: WidgetRefresh = refreshRaw === "manual" ? "manual" : "daily";
  const sizeRaw = String(r.size ?? "");
  const size: WidgetSize | undefined = (["sm", "md", "lg"] as WidgetSize[]).includes(sizeRaw as WidgetSize)
    ? (sizeRaw as WidgetSize)
    : undefined;
  return {
    id: typeof r.id === "string" && r.id ? r.id : `w_${slug(title)}`,
    title,
    kind,
    prompt,
    refresh,
    last_refreshed_at: null,
    ...(size ? { size } : {}),
  };
}

function coerceOperation(raw: unknown): DashboardOperation | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const label = typeof r.label === "string" ? r.label.trim() : "";
  if (!label) return null;
  const kindRaw = String(r.kind ?? "chat");
  const kind: OperationKind = kindRaw === "form" ? "form" : "chat";
  const op: DashboardOperation = {
    id: typeof r.id === "string" && r.id ? r.id : `op_${slug(label)}`,
    label,
    kind,
    prompt: kind === "chat" ? String(r.prompt ?? "").trim() : "",
  };
  if (kind === "form") {
    const table = typeof r.table === "string" ? r.table.trim() : "";
    if (!table) return null;
    op.table = table;
    if (r.prefill && typeof r.prefill === "object") {
      op.prefill = r.prefill as Record<string, unknown>;
    }
  } else if (!op.prompt) {
    return null;
  }
  return op;
}

export default function DashboardWizard({
  folder,
  kind,
  onApproveWidget,
  onApproveOperation,
  onCancel,
}: Props) {
  const toast = useToast();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [committing, setCommitting] = useState(false);
  // Track when the very first turn (kicked by /wizard/start) has produced
  // its reply, so we know the session is ready for follow-up turns.
  const [bootstrapped, setBootstrapped] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fence = PROPOSAL_FENCE[kind];

  // Auto-scroll the transcript on new content.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Poll the wizard session for the assistant's first reply. The first turn
  // is kicked server-side as a hidden background turn, so we have to read
  // the reply from session messages rather than streaming it. Subsequent
  // turns go through chatStream and stream normally.
  const pollFirstReply = useCallback(async (sid: string) => {
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      try {
        const detail = await getSession(sid);
        const lastAssistant = [...detail.messages].reverse().find((m) => m.role === "assistant");
        if (lastAssistant?.content) {
          // Keep the user's initial goal at the top of the transcript;
          // we only injected one user message before kicking the wizard.
          setMessages((m) => {
            const userTurns = m.filter((x) => x.role === "user");
            return [...userTurns, { role: "assistant", content: lastAssistant.content }];
          });
          setBootstrapped(true);
          setBusy(false);
          return;
        }
      } catch {
        // Network blip — retry.
      }
      await new Promise((r) => setTimeout(r, 800));
    }
    setBusy(false);
    toast.error("Wizard didn't respond in time. Try again or close.");
  }, [toast]);

  const sendInitialGoal = useCallback(async (goal: string) => {
    setBusy(true);
    setMessages([{ role: "user", content: goal }]);
    try {
      const { session_id } = await startWizard(folder, kind, goal);
      setSessionId(session_id);
      void pollFirstReply(session_id);
    } catch (e) {
      setBusy(false);
      toast.error("Couldn't start wizard", { detail: (e as Error).message });
    }
  }, [folder, kind, pollFirstReply, toast]);

  const sendFollowup = useCallback(async (text: string) => {
    if (!sessionId || !text.trim()) return;
    setBusy(true);
    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "", streaming: true },
    ]);
    try {
      let buffer = "";
      await chatStream(text, sessionId, (event) => {
        if (event.type === "delta") {
          buffer += event.text ?? "";
          setMessages((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = { ...last, content: buffer };
            }
            return next;
          });
        } else if (event.type === "done") {
          setMessages((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = { role: "assistant", content: event.reply || buffer };
            }
            return next;
          });
        }
      });
    } catch (e) {
      toast.error("Wizard turn failed", { detail: (e as Error).message });
      setMessages((m) => {
        // Remove the empty streaming placeholder we optimistically pushed.
        if (m.length && m[m.length - 1].role === "assistant" && !m[m.length - 1].content) {
          return m.slice(0, -1);
        }
        return m;
      });
    } finally {
      setBusy(false);
    }
  }, [sessionId, toast]);

  const handleSend = useCallback(() => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    if (!sessionId) {
      void sendInitialGoal(text);
    } else {
      void sendFollowup(text);
    }
  }, [draft, busy, sessionId, sendInitialGoal, sendFollowup]);

  // Find the latest assistant message and try to extract a proposal.
  type Proposal =
    | { kind: "widget"; widget: DashboardWidget; prose: string }
    | { kind: "operation"; operation: DashboardOperation; prose: string };
  const proposal = useMemo<Proposal | null>(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role !== "assistant") continue;
      if (m.streaming) return null; // wait for the stream to finish
      const extracted = extractProposal(m.content, fence);
      if (!extracted) return null;
      if (kind === "widget") {
        const w = coerceWidget(extracted.json);
        return w ? { kind: "widget", widget: w, prose: extracted.stripped } : null;
      }
      const op = coerceOperation(extracted.json);
      return op ? { kind: "operation", operation: op, prose: extracted.stripped } : null;
    }
    return null;
  }, [messages, fence, kind]);

  const handleApprove = useCallback(async () => {
    if (!proposal || committing) return;
    setCommitting(true);
    try {
      if (proposal.kind === "widget" && onApproveWidget) {
        await onApproveWidget(proposal.widget);
      } else if (proposal.kind === "operation" && onApproveOperation) {
        await onApproveOperation(proposal.operation);
      }
    } finally {
      setCommitting(false);
    }
  }, [proposal, committing, onApproveWidget, onApproveOperation]);

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div
        className="dt-modal dashboard-wizard"
        onClick={(e) => e.stopPropagation()}
        style={{ minWidth: 560, maxWidth: 720, maxHeight: "85vh", display: "flex", flexDirection: "column" }}
      >
        <div className="dt-modal-title">{TITLE[kind]}</div>

        <div className="dashboard-wizard-transcript" ref={scrollRef}>
          {messages.length === 0 && !busy && (
            <div className="data-dash-hint" style={{ whiteSpace: "pre-line" }}>
              {PLACEHOLDER[kind]}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`dashboard-wizard-msg dashboard-wizard-msg--${m.role}`}>
              <MarkdownView>{m.content || (m.streaming ? "…" : "")}</MarkdownView>
            </div>
          ))}
          {busy && !bootstrapped && messages.length === 1 && (
            <div className="dashboard-wizard-msg dashboard-wizard-msg--assistant">
              <span className="data-dash-hint">Thinking…</span>
            </div>
          )}
        </div>

        {proposal && (
          <div className="dashboard-wizard-proposal">
            <div className="dashboard-wizard-proposal-title">Proposed {kind}</div>
            <pre className="dashboard-wizard-proposal-body">
              <code>
                {proposal.kind === "widget"
                  ? JSON.stringify(proposal.widget, null, 2)
                  : JSON.stringify(proposal.operation, null, 2)}
              </code>
            </pre>
            <div className="dashboard-wizard-proposal-actions">
              <button
                type="button"
                className="data-dash-action-btn data-dash-action-btn--primary"
                onClick={() => void handleApprove()}
                disabled={committing}
              >
                {committing ? "Creating…" : `Create ${kind}`}
              </button>
              <span className="data-dash-hint" style={{ fontSize: 11.5 }}>
                Or keep chatting to refine.
              </span>
            </div>
          </div>
        )}

        <div className="dashboard-wizard-input">
          <textarea
            className="form-input form-textarea"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={messages.length === 0 ? PLACEHOLDER[kind] : "Reply to the wizard…"}
            rows={3}
            disabled={busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
          <div className="dashboard-wizard-input-actions">
            <button
              className="data-dash-action-btn"
              onClick={onCancel}
              disabled={committing}
            >
              Cancel
            </button>
            <button
              className="data-dash-action-btn data-dash-action-btn--primary"
              onClick={handleSend}
              disabled={busy || !draft.trim()}
              title="Send (⌘/Ctrl + Enter)"
            >
              {busy ? "…" : sessionId ? "Send" : "Start"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
