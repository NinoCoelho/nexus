"""Multi-bucket retention planning.

Replaces the single positional split ("keep last N, summarize the rest") with
a relevance-aware partition into five buckets:

  protected  — never compacted (system, last user/assistant, ``nx:pin``)
  recent     — keep verbatim (the trailing working set)
  relevant   — keep verbatim (older, but high relevance score)
  summarize  — collapse into the session-memory summary
  drop       — discard (scrape garbage / noise)

The hard constraint is **tool-pair integrity**: a provider rejects an
assistant message carrying ``tool_calls`` unless every referenced tool result
follows. So an assistant-with-tool_calls and its TOOL messages are grouped
into an atomic *compaction unit* and assigned to one bucket together — the
unit is never split across the recent boundary, and a kept assistant never
loses its tool result (and vice-versa: a summarized tool result takes its
assistant with it into the prose summary).

This module is pure planning: it decides *what* goes where but performs no
LLM calls and mutates no messages. ``loop/summarize.py`` consumes the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nexus.agent.llm import ChatMessage, Role
from nexus.agent.loop.relevance import MessageScore

# Head (older) messages scoring at/above this are kept verbatim as "relevant"
# even though they're outside the recent window. Tuned so a head USER message
# (role 0.18) typically clears it while a no-overlap TOOL result does not.
DEFAULT_RELEVANT_THRESHOLD = 0.30

# How many trailing messages always count as the recent working set.
DEFAULT_RECENT_K = 20


def _unit_is_garbage(messages: list[ChatMessage], unit: list[int]) -> bool:
    """A unit is garbage if any of its TOOL results is scrape noise.

    Imported lazily so this module stays importable in isolation (and so the
    scrape-noise detector can evolve without touching this file's imports).
    """
    from .compact import _looks_like_scrape_garbage

    for i in unit:
        m = messages[i]
        if m.role == Role.TOOL and m.content and _looks_like_scrape_garbage(m.content):
            return True
    return False


def _build_units(messages: list[ChatMessage]) -> list[list[int]]:
    """Group each assistant(tool_calls) with its immediately-following TOOL
    messages into an atomic index list. All other messages are singletons.

    Guarantees a unit boundary never falls between an assistant-with-tool_calls
    and its tool results.
    """
    units: list[list[int]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if msg.role == Role.ASSISTANT and msg.tool_calls:
            unit = [i]
            j = i + 1
            while j < n and messages[j].role == Role.TOOL:
                unit.append(j)
                j += 1
            units.append(unit)
            i = j
        else:
            units.append([i])
            i += 1
    return units


@dataclass
class RetentionPlan:
    """Bucket assignment as index lists, aligned with the input history.

    Use ``kept_indices()`` for the verbatim-survivor set (protected + recent +
    relevant) and ``summarize`` / ``drop`` for what gets removed from the
    structured history.
    """

    protected: list[int] = field(default_factory=list)
    recent: list[int] = field(default_factory=list)
    relevant: list[int] = field(default_factory=list)
    summarize: list[int] = field(default_factory=list)
    drop: list[int] = field(default_factory=list)

    def kept_indices(self) -> list[int]:
        """Every index that survives verbatim, in original order."""
        return sorted({*self.protected, *self.recent, *self.relevant})

    def is_noop(self) -> bool:
        """True when nothing would be removed — caller can skip summarizing."""
        return not self.summarize and not self.drop


def partition(
    messages: list[ChatMessage],
    scores: list[MessageScore],
    *,
    recent_k: int = DEFAULT_RECENT_K,
    relevant_threshold: float = DEFAULT_RELEVANT_THRESHOLD,
) -> RetentionPlan:
    """Partition message indices into retention buckets.

    Args:
        messages: Full ordered history (oldest first).
        scores: Index-aligned scores from :func:`relevance.score_messages`.
        recent_k: Trailing working-set size (unit-aligned so pairs stay whole).
        relevant_threshold: Head messages at/above this score are kept.

    The plan always respects tool-pair integrity: no assistant-with-tool_calls
    in the kept set is ever separated from its tool results.
    """
    n = len(messages)
    plan = RetentionPlan()
    if n == 0:
        return plan

    score_by_idx = {s.index: s.score for s in scores}

    units = _build_units(messages)

    # Special indices that are always protected regardless of position.
    last_user = max((i for i, m in enumerate(messages) if m.role == Role.USER), default=-1)
    last_asst = max(
        (i for i, m in enumerate(messages) if m.role == Role.ASSISTANT), default=-1
    )
    pinned = {
        i for i, m in enumerate(messages)
        if _content_has(m, "nx:pin")
    }
    systems = {i for i, m in enumerate(messages) if m.role == Role.SYSTEM}
    always_protected = {last_user, last_asst, *pinned, *systems}

    # Find the unit containing the recent boundary so the split is unit-aligned.
    target = max(0, n - recent_k)
    recent_start_unit = 0
    for ui, u in enumerate(units):
        # The recent window begins at the first unit that starts at or after
        # `target`, OR contains `target`. Either way the boundary lands on a
        # unit edge — never mid-pair.
        if u[0] >= target or target in u:
            recent_start_unit = ui
            break
    else:
        recent_start_unit = len(units)

    for ui, u in enumerate(units):
        bucket: list[int]
        if ui >= recent_start_unit:
            bucket = plan.recent
        elif always_protected.intersection(u):
            bucket = plan.protected
        elif _unit_is_garbage(messages, u):
            bucket = plan.drop
        else:
            unit_score = max((score_by_idx.get(i, 0.0) for i in u), default=0.0)
            bucket = plan.relevant if unit_score >= relevant_threshold else plan.summarize
        bucket.extend(u)

    return plan


def _content_has(msg: ChatMessage, needle: str) -> bool:
    """True if the message's flattened text contains ``needle``."""
    content = msg.content
    if content is None:
        return False
    if isinstance(content, str):
        return needle in content
    for p in content:
        if getattr(p, "kind", None) == "text" and p.text and needle in p.text:
            return True
    return False
