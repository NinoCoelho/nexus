"""Provider-side prompt caching helpers.

Every LLM call re-sends the same ~15K-token prefix: the tool JSON schemas plus
the stable part of the system prompt (identity, skills catalog, credential
lists). Nothing requested caching, so all of it was billed at full input price
on every call — including each iteration of a single multi-step turn, where the
calls are seconds apart.

``prompt_builder`` emits a ``CACHE_BREAKPOINT_MARKER`` between the prompt's
stable prefix and its volatile tail. These helpers turn that marker into the
provider-specific breakpoint:

* Anthropic — ``system`` becomes a list of text blocks, the stable one carrying
  ``cache_control: {"type": "ephemeral"}``; the last tool gets the same marker
  so the tool block is cached too.
* Bedrock Converse — the same idea with ``{"cachePoint": {"type": "default"}}``
  entries inserted into the ``system`` and ``tools`` lists.
* OpenAI-compatible providers cache automatically with no breakpoints, so they
  only need the marker stripped.

Two caveats worth knowing when reading cache metrics:

* the cached prefix must clear a provider minimum (1024 tokens for Sonnet and
  Opus class models) or the breakpoint is ignored;
* the default TTL is ~5 minutes, so iterations inside one turn hit the cache
  while work spaced further apart (a 2-hourly coordinator sweep) will not.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_ANTHROPIC_CACHE_CONTROL = {"type": "ephemeral"}
_BEDROCK_CACHE_POINT = {"cachePoint": {"type": "default"}}


def prompt_cache_enabled() -> bool:
    """Whether ``[agent].prompt_cache`` is on. Defaults to on."""
    try:
        from ...config_file import load_cached

        cfg = load_cached()
        return bool(getattr(getattr(cfg, "agent", None), "prompt_cache", True))
    except Exception:  # noqa: BLE001 — a config hiccup must not break a call
        return True


def anthropic_system_and_tools(
    system_text: str, tools: list[dict[str, Any]]
) -> tuple[Any, list[dict[str, Any]]]:
    """Return ``(system, tools)`` with Anthropic cache breakpoints applied.

    ``system`` comes back as a block list when there is a stable prefix to
    cache, or as the original string when there is nothing to split.
    """
    from ..prompt_builder import split_cache_zones, strip_cache_marker

    tools_out = list(tools)
    if tools_out:
        # Caching the tool block requires the marker on its final entry; the
        # schemas are the single largest part of the prefix.
        tools_out[-1] = {**tools_out[-1], "cache_control": dict(_ANTHROPIC_CACHE_CONTROL)}

    if not system_text:
        return "", tools_out

    stable, volatile = split_cache_zones(system_text)
    if not volatile:
        # No marker: cache the whole prompt as one block.
        return (
            [
                {
                    "type": "text",
                    "text": strip_cache_marker(stable),
                    "cache_control": dict(_ANTHROPIC_CACHE_CONTROL),
                }
            ],
            tools_out,
        )

    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": stable,
            "cache_control": dict(_ANTHROPIC_CACHE_CONTROL),
        },
        {"type": "text", "text": volatile},
    ]
    return blocks, tools_out


# Bedrock fronts many model families and rejects ``cachePoint`` on the ones
# that don't implement it, so the breakpoint is opt-in by family. The
# Anthropic provider needs no such guard — it only ever talks to Claude.
_BEDROCK_CACHE_FAMILIES = ("anthropic.", "claude", "nova")


def bedrock_supports_cache(model_id: str) -> bool:
    name = (model_id or "").lower()
    return any(fam in name for fam in _BEDROCK_CACHE_FAMILIES)


def bedrock_system_and_tools(
    system_blocks: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Insert Bedrock ``cachePoint`` entries into the system and tool lists.

    ``system_blocks`` is Converse's ``[{"text": ...}]`` shape. Only the *first*
    block is split on the marker — that is the built prompt; any later blocks
    are carried-context messages and belong on the volatile side.

    Callers must gate on :func:`bedrock_supports_cache` first.
    """
    from ..prompt_builder import split_cache_zones, strip_cache_marker

    tools_out = list(tools)
    if tools_out:
        tools_out = [*tools_out, dict(_BEDROCK_CACHE_POINT)]

    if not system_blocks:
        return [], tools_out

    first_text = str(system_blocks[0].get("text") or "")
    stable, volatile = split_cache_zones(first_text)
    rest = list(system_blocks[1:])

    if not volatile:
        out = [{"text": strip_cache_marker(stable)}, dict(_BEDROCK_CACHE_POINT), *rest]
        return out, tools_out

    return (
        [
            {"text": stable},
            dict(_BEDROCK_CACHE_POINT),
            {"text": volatile},
            *rest,
        ],
        tools_out,
    )
