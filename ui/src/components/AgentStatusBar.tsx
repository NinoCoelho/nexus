import type { SessionUsage } from "../api";
import "./AgentStatusBar.css";

interface Props {
  usage: SessionUsage | null;
  thinking?: boolean;
  onOpenTrajectory?: () => void;
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

export default function AgentStatusBar({ usage, thinking, onOpenTrajectory }: Props) {
  if (!usage) return null;
  const { model, input_tokens, output_tokens, tool_call_count } = usage;
  const total = input_tokens + output_tokens;
  if (!model && total === 0 && tool_call_count === 0) return null;
  const shortModel = model ? model.split("/").pop() : null;

  return (
    <div className={`agent-status-bar${thinking ? " is-thinking" : ""}`}>
      {shortModel && (
        <span className="agent-status-pill" title={`Model: ${model}`}>
          {shortModel}
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
      {onOpenTrajectory && (
        <button
          type="button"
          className="agent-status-pill agent-status-btn"
          onClick={onOpenTrajectory}
          title="View trajectory log for this session"
          aria-label="View trajectory log"
        >
          ⎘
        </button>
      )}
    </div>
  );
}
