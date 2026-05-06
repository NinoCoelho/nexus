import type { SessionUsage } from "../api";
import "./AgentStatusBar.css";

interface Props {
  usage: SessionUsage | null;
  thinking?: boolean;
  selectedModel?: string;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function fmtCost(c: number | null, status: SessionUsage["cost_status"]): string {
  if (c == null) return status === "unknown" ? "—" : "$0";
  if (c < 0.01) return `$${c.toFixed(4)}`;
  return `$${c.toFixed(2)}`;
}

const ZONE_COLORS: Record<string, string> = {
  green: "#22c55e",
  yellow: "#eab308",
  orange: "#f97316",
  red: "#ef4444",
};

export default function AgentStatusBar({ usage, thinking, selectedModel }: Props) {
  if (!usage) return null;
  const { input_tokens, output_tokens, tool_call_count } = usage;
  const total = input_tokens + output_tokens;
  const liveModel = selectedModel && selectedModel !== "auto" ? selectedModel : usage.model;
  if (!liveModel && total === 0 && tool_call_count === 0) return null;
  const shortModel = liveModel ? liveModel.split("/").pop() : null;
  const ctxZone = usage.context_zone ?? "unknown";
  const ctxPct = usage.context_pct ?? 0;
  const showCtx = (usage.context_window_tokens > 0 || usage.estimated_context_tokens > 0) && ctxZone !== "unknown";

  return (
    <div className={`agent-status-bar${thinking ? " is-thinking" : ""}`}>
      {shortModel && (
        <span className="agent-status-pill" title={`Model: ${liveModel}`}>
          {shortModel}
        </span>
      )}
      {showCtx && (
        <span
          className="agent-status-pill agent-status-ctx"
          style={{ borderColor: ZONE_COLORS[ctxZone] || undefined, color: ZONE_COLORS[ctxZone] || undefined }}
          title={`Context: ~${usage.estimated_context_tokens?.toLocaleString()} / ${usage.context_window_tokens?.toLocaleString()} tokens (${ctxZone})`}
        >
          <span
            className="agent-status-ctx-dot"
            style={{ background: ZONE_COLORS[ctxZone] }}
          />
          {Math.round(ctxPct * 100)}%
        </span>
      )}
      {total > 0 && (
        <span className="agent-status-pill" title={`In: ${input_tokens.toLocaleString()} · Out: ${output_tokens.toLocaleString()}`}>
          {fmtTokens(input_tokens)}↑ {fmtTokens(output_tokens)}↓
        </span>
      )}
      {tool_call_count > 0 && (
        <span className="agent-status-pill" title={`${tool_call_count} tool calls this session`}>
          {tool_call_count} tools
        </span>
      )}
      <span
        className="agent-status-pill agent-status-cost"
        title={
          usage.cost_status === "unknown"
            ? "No pricing data for this model"
            : `Estimated session cost (USD)`
        }
      >
        {fmtCost(usage.estimated_cost_usd, usage.cost_status)}
      </span>
    </div>
  );
}
