import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TraceEvent } from "../api";
import { setMessageFeedback, setMessagePin } from "../api";
import type { TimelineStep } from "./ChatView";
import MarkdownView from "./MarkdownView";

/**
 * AssistantMessage — renders a single assistant response in the chat.
 *
 * Layout: avatar + name header, then one of:
 *   1. Streaming mode (content still arriving) — shows partial text + activity strip
 *   2. Final message with timeline — expandable step-by-step breakdown
 *   3. Final message without timeline — plain rendered markdown
 *
 * Tool calls within the response are rendered as compact chips in the
 * activity strip; clicking one opens the StepDetailModal.
 * Vault links (vault://path) are intercepted and surfaced as
 * "Open in Vault" buttons via the onOpenInVault callback.
 */
import VaultFilePreview from "./VaultFilePreview";
import ActivityTimeline from "./ActivityTimeline";
import { useTTS } from "../hooks/useTTS";
import { InternalResourceRenderer } from "./StepDetailModal/ResultRenderers";
import { tryParseJson } from "./StepDetailModal/types";
import "./AssistantMessage.css";

interface Props {
  content: string;
  trace?: TraceEvent[];
  timeline?: TimelineStep[];
  timestamp: Date;
  streaming?: boolean;
  onOpenInVault?: (path: string) => void;
  model?: string;
  sessionId?: string | null;
  seq?: number;
  feedback?: "up" | "down" | null;
  onFeedbackChange?: (value: "up" | "down" | null) => void;
  pinned?: boolean;
  onPinChange?: (pinned: boolean) => void;
  thinking?: string;
  reconnecting?: {
    attempt: number;
    maxAttempts: number;
    delaySeconds: number;
    reason: string;
  };
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const INLINE_TOOLS = new Set(["show_kanban", "show_dashboard_widget", "show_data_table"]);

function inlineResourcesFromTrace(trace?: TraceEvent[]): { uri: string; toolResult: unknown }[] {
  if (!trace) return [];
  const out: { uri: string; toolResult: unknown }[] = [];
  for (const ev of trace) {
    const tool = ev.tool ?? ((ev as unknown as Record<string, unknown>).name as string | undefined);
    const result = ev.result ?? (ev as unknown as Record<string, unknown>).preview;
    if (!tool || !INLINE_TOOLS.has(tool) || result == null) continue;
    const parsed = tryParseJson(result);
    if (parsed && typeof parsed === "object") {
      const uri = (parsed as Record<string, unknown>).resourceUri as string | undefined;
      if (uri && uri.startsWith("ui://nexus/")) {
        out.push({ uri, toolResult: parsed });
      }
    }
  }
  return out;
}

export default function AssistantMessage({ content, trace, timeline, timestamp, streaming, onOpenInVault, model, sessionId, seq, feedback, onFeedbackChange, pinned, onPinChange, thinking, reconnecting }: Props) {
  const { t } = useTranslation("chat");
  const [copied, setCopied] = useState(false);
  const tts = useTTS();
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [localFeedback, setLocalFeedback] = useState<"up" | "down" | null>(feedback ?? null);
  const [localPinned, setLocalPinned] = useState<boolean>(!!pinned);
  const [thinkingOpen, setThinkingOpen] = useState(false);
  useEffect(() => { setLocalFeedback(feedback ?? null); }, [feedback]);
  useEffect(() => { setLocalPinned(!!pinned); }, [pinned]);

  const processed = useMemo(() => content, [content]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // clipboard may be blocked
    }
  }

  const canFeedback = !!sessionId && typeof seq === "number" && !streaming;
  async function handlePin() {
    if (!canFeedback || !sessionId || typeof seq !== "number") return;
    const next = !localPinned;
    setLocalPinned(next);
    onPinChange?.(next);
    try {
      await setMessagePin(sessionId, seq, next);
    } catch {
      setLocalPinned(!next);
      onPinChange?.(!next);
    }
  }
  async function handleFeedback(value: "up" | "down") {
    if (!canFeedback || !sessionId || typeof seq !== "number") return;
    const next = localFeedback === value ? null : value;
    setLocalFeedback(next);
    onFeedbackChange?.(next);
    try {
      await setMessageFeedback(sessionId, seq, next);
    } catch {
      // revert on failure
      setLocalFeedback(localFeedback);
      onFeedbackChange?.(localFeedback);
    }
  }

  return (
    <div className="asst-msg">
      <div className="asst-header">
        <div className="asst-avatar" aria-hidden="true" />
        <span className="asst-name">{t("chat:assistant.name")}</span>
        {model && (
          <span className="asst-model-badge">{t("chat:assistant.via", { model: model.split("/").pop() })}</span>
        )}
        <span className="asst-time">{fmt(timestamp)}</span>
      </div>
      <div className="asst-card">
        {thinking && thinking.length > 0 && (
          <details
            className="asst-thinking"
            open={thinkingOpen}
            onToggle={(e) => setThinkingOpen((e.target as HTMLDetailsElement).open)}
          >
            <summary>
              {streaming ? t("chat:assistant.thinkingStreaming") : t("chat:assistant.thinking")}
              <span className="asst-thinking-count"> {t("chat:assistant.thinkingCount", { count: thinking.length })}</span>
            </summary>
            <pre className="asst-thinking-body">{thinking}</pre>
          </details>
        )}
        {reconnecting && (
          <div className="asst-reconnecting" role="status" aria-live="polite">
            <span className="asst-reconnecting-spinner" aria-hidden="true" />
            <span className="asst-reconnecting-text">
              {t("chat:assistant.reconnecting", {
                attempt: reconnecting.attempt,
                max: reconnecting.maxAttempts,
                reason: reconnecting.reason || "transient error",
                delay: Math.round(reconnecting.delaySeconds),
                defaultValue: "Reconnecting after {{reason}}… (attempt {{attempt}}/{{max}}, retrying in {{delay}}s)",
              })}
            </span>
          </div>
        )}
        <ActivityTimeline steps={timeline} trace={trace} streaming={!!streaming} sessionId={sessionId} />
        {inlineResourcesFromTrace(trace).map((r, i) => (
          <div key={`inline-res-${i}`} className="asst-inline-resource">
            <InternalResourceRenderer resourceUri={r.uri} toolResult={r.toolResult} />
          </div>
        ))}
        <div className="asst-body">
          <MarkdownView
            linkifyVaultPaths
            onVaultLinkPreview={setPreviewPath}
          >
            {processed}
          </MarkdownView>
        </div>
        <div className="asst-footer">
          {canFeedback && (
            <>
              <button
                className={`bubble-action-btn${localPinned ? " is-active" : ""}`}
                onClick={handlePin}
                title={localPinned ? t("chat:assistant.unpin") : t("chat:assistant.pin")}
                aria-label={t("chat:assistant.pinAria")}
                aria-pressed={localPinned}
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill={localPinned ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 2h6l-1 4 2 3H4l2-3z" />
                  <line x1="8" y1="9" x2="8" y2="14" />
                </svg>
              </button>
              <button
                className={`bubble-action-btn${localFeedback === "up" ? " is-active" : ""}`}
                onClick={() => handleFeedback("up")}
                title={localFeedback === "up" ? t("chat:assistant.removeThumbsUp") : t("chat:assistant.helpful")}
                aria-label={t("chat:assistant.markHelpfulAria")}
                aria-pressed={localFeedback === "up"}
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill={localFeedback === "up" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 7v6H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h3z" />
                  <path d="M6 7l3-5a1.5 1.5 0 0 1 2.8.9V6h2.4a1.5 1.5 0 0 1 1.5 1.7l-.7 4.5A1.5 1.5 0 0 1 13.5 13.5H6" />
                </svg>
              </button>
              <button
                className={`bubble-action-btn${localFeedback === "down" ? " is-active" : ""}`}
                onClick={() => handleFeedback("down")}
                title={localFeedback === "down" ? t("chat:assistant.removeThumbsDown") : t("chat:assistant.notHelpful")}
                aria-label={t("chat:assistant.markNotHelpfulAria")}
                aria-pressed={localFeedback === "down"}
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill={localFeedback === "down" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M10 9V3h3a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-3z" />
                  <path d="M10 9l-3 5a1.5 1.5 0 0 1-2.8-.9V10H1.8A1.5 1.5 0 0 1 .3 8.3L1 3.8A1.5 1.5 0 0 1 2.5 2.5H10" />
                </svg>
              </button>
            </>
          )}
          {tts.available && content && !streaming && (
            <button
              className={`bubble-action-btn${tts.state === "playing" ? " is-active" : ""}`}
              onClick={() => {
                if (tts.state === "idle") void tts.speak(content);
                else tts.stop();
              }}
              title={
                tts.state === "playing"
                  ? "Stop reading"
                  : tts.state === "loading"
                  ? "Loading…"
                  : "Read aloud"
              }
              aria-label="Read message aloud"
              aria-pressed={tts.state === "playing"}
              disabled={tts.state === "loading"}
            >
              {tts.state === "playing" ? (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                  <rect x="3" y="3" width="4" height="10" rx="0.5" />
                  <rect x="9" y="3" width="4" height="10" rx="0.5" />
                </svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6h2l3-3v10l-3-3H3z" fill="currentColor" />
                  <path d="M11 5.5a3.5 3.5 0 0 1 0 5" />
                  <path d="M13 3.5a6 6 0 0 1 0 9" />
                </svg>
              )}
            </button>
          )}
          <button
            className="bubble-action-btn"
            onClick={handleCopy}
            title={copied ? t("chat:assistant.copied") : t("chat:assistant.copyMarkdown")}
            aria-label={t("chat:assistant.copyMarkdownAria")}
          >
            {copied ? (
              <>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 8 7 12 13 4" />
                </svg>
                <span>{t("chat:assistant.copied")}</span>
              </>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="5" width="8" height="9" rx="1.5" />
                  <path d="M3 10V3a1 1 0 0 1 1-1h7" />
                </svg>
                <span>{t("chat:assistant.copy")}</span>
              </>
            )}
          </button>
        </div>
      </div>
      <VaultFilePreview
        path={previewPath}
        onClose={() => setPreviewPath(null)}
        onOpenInVault={onOpenInVault}
      />
    </div>
  );
}
