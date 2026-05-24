"""Per-turn tool-result token budget enforcement.

After each tool result is appended to the agent loop's working_messages,
the loop checks cumulative tool-result tokens against a configurable
threshold. If exceeded, a system hint is injected telling the agent to
synthesize with what it has instead of calling more tools.

This prevents any single tool (or combination of tools) from overpopulating
the context window between LLM iterations.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_TOOL_BUDGET_TOKENS = 15_000
DEFAULT_MAX_SCRAPE_CALLS = 3
DEFAULT_SESSION_TOOL_BUDGET_TOKENS = 50_000


@dataclass
class BudgetCheck:
    exceeded: bool
    cumulative_tool_tokens: int
    budget: int
    call_counts: dict[str, int] = field(default_factory=dict)
    call_limit_exceeded: tuple[str, int] | None = None


def estimate_tool_result_tokens(text: str) -> int:
    """Estimate tokens in a single tool result string.

    Uses the same heuristic as overflow.py: ~4 chars/token for ASCII,
    ~3 chars/token for dense/non-ASCII content.
    """
    if not text:
        return 0
    sample = text[:512]
    non_ascii = sum(1 for c in sample if ord(c) > 127)
    ratio = 3 if (non_ascii / max(1, len(sample)) > 0.05 or sample.lstrip()[:1] in ("[", "{")) else 4
    return len(text) // ratio


def check_tool_budget(
    cumulative_tool_tokens: int,
    new_result_text: str,
    budget: int = DEFAULT_TOOL_BUDGET_TOKENS,
    call_counts: dict[str, int] | None = None,
    tool_name: str = "",
    call_limits: dict[str, int] | None = None,
) -> BudgetCheck:
    """Check whether adding a new tool result exceeds the per-turn budget.

    Args:
        cumulative_tool_tokens: Tokens accumulated from previous tool results
            in this turn.
        new_result_text: The tool result text about to be added.
        budget: Maximum cumulative tool-result tokens for a single turn.
        call_counts: Cumulative per-tool call counts so far (modified in-place).
        tool_name: Name of the tool that produced this result.
        call_limits: Per-tool call limits, e.g. {"web_scrape": 3}.

    Returns:
        BudgetCheck with exceeded flag, updated cumulative count, and
        updated call_counts.
    """
    if call_counts is None:
        call_counts = {}
    if call_limits is None:
        call_limits = {}
    if tool_name:
        call_counts[tool_name] = call_counts.get(tool_name, 0) + 1

    new_tokens = estimate_tool_result_tokens(new_result_text)
    total = cumulative_tool_tokens + new_tokens
    token_exceeded = total > budget if budget > 0 else False

    call_limit_hit: tuple[str, int] | None = None
    if tool_name and tool_name in call_limits:
        limit = call_limits[tool_name]
        count = call_counts[tool_name]
        if count >= limit:
            call_limit_hit = (tool_name, count)

    exceeded = token_exceeded or call_limit_hit is not None
    return BudgetCheck(
        exceeded=exceeded,
        cumulative_tool_tokens=total,
        budget=budget,
        call_counts=call_counts,
        call_limit_exceeded=call_limit_hit,
    )


BUDGET_EXCEEDED_HINT = (
    "\n\n[CRITICAL: Tool budget reached — cumulative tool results have consumed "
    "the per-turn token allocation. You MUST NOT call any more tools this turn. "
    "Synthesize your answer immediately using only the information already "
    "available in this conversation. Do not attempt additional searches, "
    "scrapes, or API calls.]"
)

CALL_LIMIT_EXCEEDED_HINT = (
    "\n\n[CRITICAL: Tool call limit reached — {count} {tool} calls in this turn. "
    "You MUST NOT call any more tools this turn. Synthesize your answer "
    "immediately using only the information already available. Do not attempt "
    "additional searches, scrapes, or API calls.]"
)


def estimate_session_tool_tokens(history) -> int:
    """Estimate total tool-result tokens across a session history.

    Scans for TOOL-role messages and sums estimated tokens. Used by the
    cross-turn budget check to decide whether to trigger compaction.
    """
    from ..llm.types import Role

    total = 0
    for msg in history:
        if getattr(msg, "role", None) == Role.TOOL and msg.content:
            total += estimate_tool_result_tokens(msg.content)
    return total
