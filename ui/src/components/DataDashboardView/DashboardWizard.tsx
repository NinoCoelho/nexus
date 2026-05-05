import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { chatStream } from "../../api/chat";
import {
  previewWidget,
  startWizard,
  type DashboardWidget,
  type DashboardOperation,
  type VizType,
  type WidgetRefresh,
  type WidgetSize,
  type VizConfig,
  type WidgetQueryResult,
  type OperationKind,
  type WizardKind,
} from "../../api/dashboard";
import { getSession } from "../../api/sessions";
import MarkdownView from "../MarkdownView";
import { useToast } from "../../toast/ToastProvider";
import { getVizComponent } from "./viz";
import type { VizConfig as VizVizConfig } from "./viz/types";

const MAX_RETRIES = 3;

interface Props {
  folder: string;
  kind: WizardKind;
  editing?: DashboardWidget | null;
  initialGoal?: string | null;
  onApproveWidget?: (widget: DashboardWidget) => void | Promise<void>;
  onApproveOperation?: (op: DashboardOperation) => void | Promise<void>;
  onCancel: () => void;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  autoRetry?: boolean;
}

const PROPOSAL_FENCE: Record<WizardKind, string> = {
  widget: "nexus-widget-proposal",
  operation: "nexus-operation-proposal",
};

const PLACEHOLDER_CREATE: Record<WizardKind, string> = {
  widget: "What do you want to see?\nE.g. \"Patient visits per quarter as a bar chart\"",
  operation: "What action do you want to add?\nE.g. \"A button that summarizes the last patient seen\"",
};

const VIZ_TYPE_LABEL: Record<string, string> = {
  bar: "Bar chart",
  line: "Line chart",
  area: "Area chart",
  pie: "Pie chart",
  donut: "Donut chart",
  table: "Table",
  kpi: "KPI card",
};

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

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

interface WidgetProposal extends DashboardWidget {
  summary?: string;
  alternatives?: VizType[];
}

function coerceWidget(raw: unknown): WidgetProposal | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const title = typeof r.title === "string" ? r.title.trim() : "";
  const query = typeof r.query === "string" ? r.query.trim() : "";
  if (!title) return null;
  const vizTypeRaw = String(r.viz_type ?? r.kind ?? "bar");
  const validVizTypes: VizType[] = ["bar", "line", "area", "pie", "donut", "table", "kpi"];
  const viz_type: VizType = validVizTypes.includes(vizTypeRaw as VizType)
    ? (vizTypeRaw as VizType)
    : "bar";
  const refreshRaw = String(r.refresh ?? "daily");
  const refresh: WidgetRefresh = refreshRaw === "manual" ? "manual" : "daily";
  const sizeRaw = String(r.size ?? "");
  const size: WidgetSize | undefined = (["sm", "md", "lg"] as WidgetSize[]).includes(sizeRaw as WidgetSize)
    ? (sizeRaw as WidgetSize)
    : undefined;
  const queryTables = Array.isArray(r.query_tables)
    ? r.query_tables.filter((t: unknown) => typeof t === "string")
    : undefined;
  const viz_config = (r.viz_config && typeof r.viz_config === "object")
    ? (r.viz_config as VizConfig)
    : undefined;
  const prompt = typeof r.prompt === "string" ? r.prompt.trim() : undefined;
  const summary = typeof r.summary === "string" ? r.summary.trim() : undefined;
  const alternatives = Array.isArray(r.alternatives)
    ? r.alternatives.filter(
        (v: unknown): v is VizType => typeof v === "string" && validVizTypes.includes(v as VizType) && v !== viz_type,
      )
    : undefined;
  return {
    id: typeof r.id === "string" && r.id ? r.id : `w_${slug(title)}`,
    title,
    viz_type,
    query,
    ...(queryTables?.length ? { query_tables: queryTables as string[] } : {}),
    ...(viz_config ? { viz_config } : {}),
    ...(prompt ? { prompt } : {}),
    refresh,
    last_refreshed_at: null,
    ...(size ? { size } : {}),
    ...(summary ? { summary } : {}),
    ...(alternatives?.length ? { alternatives } : {}),
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
  editing,
  initialGoal,
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
  const [bootstrapped, setBootstrapped] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fence = PROPOSAL_FENCE[kind];

  const [previewResult, setPreviewResult] = useState<WidgetQueryResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [activeVizType, setActiveVizType] = useState<VizType | null>(null);

  const retryCountRef = useRef(0);
  const [autoRetrying, setAutoRetrying] = useState(false);
  const lastWidgetProposalRef = useRef<WidgetProposal | null>(null);
  const initialGoalSent = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const pollFirstReply = useCallback(async (sid: string) => {
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      try {
        const detail = await getSession(sid);
        const lastAssistant = [...detail.messages].reverse().find((m) => m.role === "assistant");
        if (lastAssistant?.content) {
          setMessages((m) => {
            const userTurns = m.filter((x) => x.role === "user");
            return [...userTurns, { role: "assistant", content: lastAssistant.content }];
          });
          setBootstrapped(true);
          setBusy(false);
          return;
        }
      } catch {
        // retry
      }
      await new Promise((r) => setTimeout(r, 800));
    }
    setBusy(false);
    toast.error("Wizard didn't respond in time. Try again or close.");
  }, [toast]);

  const sendInitialGoal = useCallback(async (goal: string) => {
    setBusy(true);
    setPreviewResult(null);
    setPreviewError(null);
    setPreviewLoading(false);
    setActiveVizType(null);
    retryCountRef.current = 0;
    setAutoRetrying(false);
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

  const sendFollowup = useCallback(async (text: string, opts?: { autoRetry?: boolean }) => {
    if (!sessionId || !text.trim()) return;
    setBusy(true);
    setPreviewResult(null);
    setPreviewError(null);
    setPreviewLoading(false);
    setActiveVizType(null);
    setMessages((m) => [
      ...m,
      { role: "user", content: text, autoRetry: opts?.autoRetry },
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
    retryCountRef.current = 0;
    setAutoRetrying(false);
    if (!sessionId) {
      void sendInitialGoal(text);
    } else {
      void sendFollowup(text);
    }
  }, [draft, busy, sessionId, sendInitialGoal, sendFollowup]);

  useEffect(() => {
    if (initialGoal && !initialGoalSent.current) {
      initialGoalSent.current = true;
      void sendInitialGoal(initialGoal);
    }
  }, [initialGoal, sendInitialGoal]);

  type Proposal =
    | { kind: "widget"; widget: WidgetProposal; prose: string }
    | { kind: "operation"; operation: DashboardOperation; prose: string };
  const proposal = useMemo<Proposal | null>(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role !== "assistant") continue;
      if (m.streaming) return null;
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

  useEffect(() => {
    if (proposal?.kind === "widget") {
      lastWidgetProposalRef.current = proposal.widget;
    }
  }, [proposal]);

  useEffect(() => {
    if (proposal?.kind !== "widget") return;
    const w = proposal.widget;
    const viz = activeVizType ?? w.viz_type;
    if (!w.query) return;

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    void (async () => {
      try {
        const result = await previewWidget(folder, {
          query: w.query,
          viz_type: viz,
          viz_config: w.viz_config,
          query_tables: w.query_tables,
        });
        if (cancelled) return;
        if (result.error) {
          if (retryCountRef.current < MAX_RETRIES) {
            retryCountRef.current++;
            setAutoRetrying(true);
            setPreviewLoading(true);
            const retryMsg =
              `The query execution failed with this error:\n${result.error}\n\n` +
              "Please fix the SQL and output a corrected nexus-widget-proposal. " +
              "Common issues: table 't' doesn't exist (use the actual table name, not 't'), " +
              "unsupported strftime specifiers, column name typos.";
            void sendFollowup(retryMsg, { autoRetry: true });
          } else {
            setAutoRetrying(false);
            setPreviewError(result.error);
            setPreviewLoading(false);
          }
        } else {
          retryCountRef.current = 0;
          setAutoRetrying(false);
          setPreviewResult(result);
          setPreviewLoading(false);
        }
      } catch (e) {
        if (cancelled) return;
        if (retryCountRef.current < MAX_RETRIES) {
          retryCountRef.current++;
          setAutoRetrying(true);
          setPreviewLoading(true);
          const retryMsg =
            `The query execution failed with this error:\n${(e as Error).message}\n\n` +
            "Please fix the SQL and output a corrected nexus-widget-proposal.";
          void sendFollowup(retryMsg, { autoRetry: true });
        } else {
          setAutoRetrying(false);
          setPreviewError((e as Error).message);
          setPreviewLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [proposal, activeVizType, folder, sendFollowup]);

  const handleApprove = useCallback(async () => {
    if (!proposal || committing) return;
    setCommitting(true);
    try {
      if (proposal.kind === "widget" && onApproveWidget) {
        const finalVizType = activeVizType ?? proposal.widget.viz_type;
        const widget: DashboardWidget = {
          ...proposal.widget,
          viz_type: finalVizType,
        };
        await onApproveWidget(widget);
      } else if (proposal.kind === "operation" && onApproveOperation) {
        await onApproveOperation(proposal.operation);
      }
    } finally {
      setCommitting(false);
    }
  }, [proposal, committing, onApproveWidget, onApproveOperation, activeVizType]);

  const displayWidget = proposal?.kind === "widget"
    ? proposal.widget
    : autoRetrying
      ? lastWidgetProposalRef.current
      : null;

  const hasPreview = displayWidget != null;
  const widgetProposal = displayWidget;
  const vizOptions = useMemo<VizType[]>(() => {
    if (!widgetProposal) return [];
    const primary = widgetProposal.viz_type;
    const alts = widgetProposal.alternatives ?? [];
    return [primary, ...alts];
  }, [widgetProposal]);
  const currentViz = activeVizType ?? widgetProposal?.viz_type ?? null;
  const title = editing ? `Edit: ${editing.title}` : kind === "widget" ? "Create widget" : "Create action";
  const placeholder = editing
    ? "What would you like to change about this widget?"
    : PLACEHOLDER_CREATE[kind];

  return (
    <div className="dt-modal-overlay" onClick={onCancel}>
      <div
        className="dt-modal dashboard-wizard"
        onClick={(e) => e.stopPropagation()}
        style={{ minWidth: 560, maxWidth: 720, maxHeight: "90vh", display: "flex", flexDirection: "column" }}
      >
        <div className="dt-modal-title">{title}</div>

        <div className="dashboard-wizard-transcript" ref={scrollRef}>
          {messages.length === 0 && !busy && (
            <div className="data-dash-hint" style={{ whiteSpace: "pre-line" }}>
              {placeholder}
            </div>
          )}
          {messages.map((m, i) => {
            const extracted = m.role === "assistant" && !m.streaming
              ? extractProposal(m.content, fence)
              : null;
            const displayContent = extracted ? extracted.stripped : m.content;
            if (m.autoRetry) {
              return (
                <div key={i} className="dashboard-wizard-msg dashboard-wizard-msg--system">
                  <MarkdownView>{displayContent || (m.streaming ? "…" : "")}</MarkdownView>
                </div>
              );
            }
            return (
              <div key={i} className={`dashboard-wizard-msg dashboard-wizard-msg--${m.role}`}>
                <MarkdownView>{displayContent || (m.streaming ? "…" : "")}</MarkdownView>
              </div>
            );
          })}
          {busy && !bootstrapped && messages.length === 1 && (
            <div className="dashboard-wizard-msg dashboard-wizard-msg--assistant">
              <span className="data-dash-hint">Thinking…</span>
            </div>
          )}
        </div>

        {hasPreview && widgetProposal && (
          <div className="dashboard-wizard-proposal">
            <div className="dashboard-wizard-proposal-title">
              {widgetProposal.title}
            </div>

            {widgetProposal.summary && !autoRetrying && (
              <div className="data-dash-hint" style={{ fontSize: 12.5, marginBottom: 2 }}>
                {widgetProposal.summary}
              </div>
            )}

            {vizOptions.length > 1 && !autoRetrying && (
              <div className="wizard-viz-pills" role="group">
                {vizOptions.map((vt) => (
                  <button
                    key={vt}
                    type="button"
                    className={`wizard-viz-pill${vt === currentViz ? " wizard-viz-pill--active" : ""}`}
                    onClick={() => setActiveVizType(vt === widgetProposal.viz_type ? null : vt)}
                    disabled={previewLoading}
                  >
                    {VIZ_TYPE_LABEL[vt] ?? vt}
                  </button>
                ))}
              </div>
            )}

            <div className="wizard-preview-chart">
              {(previewLoading || autoRetrying) && !previewError && (
                <div className="data-dash-hint" style={{ padding: 16, textAlign: "center" }}>
                  {autoRetrying
                    ? `Fixing query… (attempt ${retryCountRef.current}/${MAX_RETRIES})`
                    : "Loading preview…"}
                </div>
              )}
              {!previewLoading && !autoRetrying && previewError && (
                <div className="widget-error-card">
                  <div className="widget-error-card-msg">Query failed</div>
                  <div className="widget-error-card-detail">{previewError}</div>
                </div>
              )}
              {!previewLoading && !autoRetrying && !previewError && previewResult && previewResult.rows.length > 0 && currentViz && (
                <WidgetPreviewRenderer
                  vizType={currentViz}
                  result={previewResult}
                  config={(widgetProposal.viz_config as VizVizConfig) ?? {}}
                />
              )}
              {!previewLoading && !autoRetrying && !previewError && previewResult && previewResult.rows.length === 0 && (
                <div className="data-dash-hint">Query returned 0 rows.</div>
              )}
            </div>

            <div className="dashboard-wizard-proposal-actions">
              <button
                type="button"
                className="data-dash-action-btn data-dash-action-btn--primary"
                onClick={() => void handleApprove()}
                disabled={committing || previewLoading || autoRetrying || !!previewError || (!!previewResult && previewResult.error !== undefined)}
              >
                {committing ? "Creating…" : "Add to dashboard"}
              </button>
              <span className="data-dash-hint" style={{ fontSize: 11.5 }}>
                Or keep chatting to refine.
              </span>
            </div>
          </div>
        )}

        {proposal?.kind === "operation" && (
          <div className="dashboard-wizard-proposal">
            <div className="dashboard-wizard-proposal-title">Proposed operation</div>
            <div className="dashboard-wizard-proposal-body">
              <code>{JSON.stringify(proposal.operation, null, 2)}</code>
            </div>
            <div className="dashboard-wizard-proposal-actions">
              <button
                type="button"
                className="data-dash-action-btn data-dash-action-btn--primary"
                onClick={() => void handleApprove()}
                disabled={committing}
              >
                {committing ? "Creating…" : "Create operation"}
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
            placeholder={messages.length === 0 ? placeholder : "Reply to the wizard…"}
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
              title="Send (Cmd/Ctrl + Enter)"
            >
              {busy ? "…" : sessionId ? "Send" : "Start"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function WidgetPreviewRenderer({
  vizType,
  result,
  config,
}: {
  vizType: VizType;
  result: WidgetQueryResult;
  config: VizVizConfig;
}) {
  const Component = getVizComponent(vizType);
  return (
    <Component
      columns={result.columns}
      rows={result.rows}
      config={config}
    />
  );
}
