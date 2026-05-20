"""Per-turn tool-result token budget enforcement.

After each tool result is appended to the agent loop's working_messages,
the loop checks cumulative tool-result tokens against a configurable
threshold. If exceeded, a system hint is injected telling the agent to
synthesize with what it has instead of calling more tools.

This prevents any single tool (or combination of tools) from overpopulating
the context window between LLM iterations.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_TOOL_BUDGET_TOKENS = 50_000


@dataclass
class BudgetCheck:
    exceeded: bool
    cumulative_tool_tokens: int
    budget: int


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
) -> BudgetCheck:
    """Check whether adding a new tool result exceeds the per-turn budget.

    Args:
        cumulative_tool_tokens: Tokens accumulated from previous tool results
            in this turn.
        new_result_text: The tool result text about to be added.
        budget: Maximum cumulative tool-result tokens for a single turn.

    Returns:
        BudgetCheck with exceeded flag and updated cumulative count.
    """
    new_tokens = estimate_tool_result_tokens(new_result_text)
    total = cumulative_tool_tokens + new_tokens
    return BudgetCheck(
        exceeded=total > budget,
        cumulative_tool_tokens=total,
        budget=budget,
    )


BUDGET_EXCEEDED_HINT = (
    "\n\n[Tool budget reached: cumulative tool results are approaching the "
    "context limit. Synthesize your answer with the information you already "
    "have. Do not call any more tools unless absolutely critical.]"
)
