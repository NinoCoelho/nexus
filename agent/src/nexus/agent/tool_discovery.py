"""Progressive tool discovery — advertise a core set, serve the rest on demand.

Every LLM call re-sends the full tool JSON-schema block; with ~46 tools that
is ~15K tokens per call, of which a typical turn uses three or four. This
module implements the pattern the tool-retrieval literature (RAG-MCP,
ScaleMCP) and production agents converge on:

- A small **core set** is always advertised in the tool block (cheap, cached).
- Everything else stays **dispatchable but hidden**: the registry keeps every
  handler, only ``specs()`` is filtered.
- A single **``tool_search``** tool returns the full schemas of matching
  hidden tools in its *result text* (not by rebuilding the request), so the
  cached prefix survives discovery.

Two advertisement policies (``[agent].tool_advertisement``):

- ``"sticky"`` (default, safe on every provider): once discovered, a tool is
  appended to the advertised set for the rest of the session. Providers that
  constrain generation to declared tool names keep working. Costs ~300
  tokens per discovered tool and one prompt-cache re-write each.
- ``"pure"``: the advertised set never changes after session start. Zero
  overhead and a byte-identical cached prefix, but it relies on the provider
  passing through tool calls to names that were not declared (Anthropic
  does; some OpenAI-compat servers with constrained decoding do not).

Safety properties (inherited from how this registry already works):

- Hidden tools remain fully dispatchable — the skill-name redirect and the
  unknown-tool helper in ``_loom_bridge.registry`` prove undeclared tool
  calls reach handlers here and get handled.
- The coordinator's allowlisted registry never installs discovery (it is
  already minimal and must stay exactly as allowlisted).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from loom.tools.base import ToolHandler, ToolResult
from loom.types import ToolSpec

from ..agent.context import CURRENT_SESSION_ID

log = logging.getLogger(__name__)


# ── tool_search spec ─────────────────────────────────────────────────────────


TOOL_SEARCH_TOOL = ToolSpec(
    name="tool_search",
    description=(
        "Search Nexus's extended tool catalog. Only core tools are in your "
        "advertised tool list; everything else (kanban, calendar, OCR, "
        "browser, dashboards, HTTP, web search/scrape, MCP tools, …) is "
        "discoverable here. Call with keywords to receive the full JSON "
        "schemas of matching tools, then call them directly by name."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords for what you need (e.g. 'kanban board tasks', 'read a webpage')",
            },
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact tool names to fetch schemas for (skips ranking)",
            },
        },
        "required": [],
    },
)


class _ToolSearchHandler(ToolHandler):
    """Minimal adapter so tool_discovery needs no import from _loom_bridge."""

    def __init__(self) -> None:
        self._spec = TOOL_SEARCH_TOOL

    @property
    def tool(self) -> ToolSpec:
        return self._spec

    async def invoke(self, args: dict) -> ToolResult:
        return ToolResult(text=handle_tool_search(args))


# ── state ────────────────────────────────────────────────────────────────────

# Defaults chosen so a normal turn never needs discovery: I/O primitives,
# skills, sub-agents and the context tools. Everything else (per-domain and
# occasionally-used tools) hides behind tool_search.
_DEFAULT_CORE = frozenset({
    "ask_user", "notify_user", "page", "terminal",
    "vault_read", "vault_write", "vault_list", "vault_search",
    "skills_list", "skill_view",
    "spawn_subagents",
    "context_status", "fork_session",
    "history_recall", "tool_search",
})

_INSTALLED_REGISTRY: Any = None
_MODE: str = "sticky"
_CORE: frozenset[str] = _DEFAULT_CORE
_STICKY_MAX: int = 28
# session_id -> ordered set of discovered tool names (dict used as ordered set)
_STICKY: dict[str, dict[str, None]] = {}
_STICKY_SESSID_CAP = 64  # bound the map: drop the oldest session's set
_top_k = 4
_lock = threading.Lock()


def _current_session_id() -> str:
    try:
        return CURRENT_SESSION_ID.get() or ""
    except LookupError:
        return ""


def _advertised_names() -> set[str]:
    """Names the LLM sees in the tool block for the current call."""
    allowed = set(_CORE)
    if _MODE != "sticky":
        return allowed
    disc = _STICKY.get(_current_session_id())
    if disc:
        for name in disc:
            if len(allowed) >= _STICKY_MAX:
                break
            allowed.add(name)
    return allowed


def _record_discovery(names: list[str]) -> None:
    """Session-sticky advertisement (no-op in pure mode)."""
    if _MODE != "sticky" or not names:
        return
    sid = _current_session_id()
    if not sid:
        return
    with _lock:
        if len(_STICKY) >= _STICKY_SESSID_CAP and sid not in _STICKY:
            _STICKY.pop(next(iter(_STICKY)))
        disc = _STICKY.setdefault(sid, {})
        for name in names:
            if len(_CORE) + len(disc) >= _STICKY_MAX:
                log.info("tool discovery: sticky cap %d reached; %s stays result-only", _STICKY_MAX, name)
                break
            disc.setdefault(name, None)


# ── ranking ──────────────────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _rank_hidden(hidden: dict[str, Any], query: str) -> list[tuple[int, str]]:
    """Keyword score hidden tools against ``query``; best first."""
    terms = _tokenize(query)
    if not terms:
        return []
    scored: list[tuple[int, str]] = []
    for name, handler in hidden.items():
        spec = handler.tool
        text = f"{name} {getattr(spec, 'description', '') or ''}".lower()
        hay = set(_tokenize(text))
        name_tokens = set(_tokenize(name))
        score = 0
        for t in terms:
            if t in name_tokens:
                score += 5
            elif t in hay:
                score += 2
            elif any(tok.startswith(t) for tok in name_tokens if len(tok) > len(t)):
                score += 1
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return scored


def _spec_json(handler: Any) -> dict:
    spec = handler.tool
    return {
        "name": spec.name,
        "description": getattr(spec, "description", "") or "",
        "parameters": getattr(spec, "parameters", {}) or {},
    }


def _blurb(description: str, limit: int = 100) -> str:
    text = " ".join((description or "").split())
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit + 1)
    if cut >= limit // 3:
        return text[: cut + 1]
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


# ── handler ──────────────────────────────────────────────────────────────────


def handle_tool_search(args: dict[str, Any]) -> str:
    reg = _INSTALLED_REGISTRY
    if reg is None:
        return json.dumps({"ok": False, "error": "tool_search not installed"}, ensure_ascii=False)
    advertised = _advertised_names()
    handlers = getattr(reg, "_handlers", {}) or {}
    hidden = {n: h for n, h in handlers.items() if n not in advertised}
    if not hidden:
        return json.dumps({"ok": True, "tools": [], "note": "No hidden tools — everything is already advertised."}, ensure_ascii=False)

    wanted = [str(n) for n in (args.get("names") or [])]
    query = str(args.get("query") or "").strip()
    selected: dict[str, Any] = {}
    already_advertised: list[str] = []
    for n in wanted:
        if n in handlers and n not in advertised:
            selected[n] = handlers[n]
        elif n in advertised:
            already_advertised.append(n)
    if not selected and query:
        for _, name in _rank_hidden(hidden, query)[:_top_k]:
            selected[name] = hidden[name]
    if not selected and not query:
        # Bare call → compact directory of hidden tools (no schemas).
        catalog = [
            {"name": n, "blurb": _blurb(getattr(hidden[n].tool, "description", "") or "")}
            for n in sorted(hidden)
        ]
        return json.dumps({
            "ok": True,
            "directory": catalog,
            "note": "Hidden tool names+blurbs. Call again with query= or names= to receive full schemas.",
        }, ensure_ascii=False)
    if not selected:
        return json.dumps({
            "ok": False,
            "error": f"no hidden tool matches {query!r}",
            "hint": "Try different keywords, or call with no arguments for the full directory.",
        }, ensure_ascii=False)

    discovered = sorted(selected)
    _record_discovery(discovered)
    if _MODE == "sticky":
        note = (
            "These tools were outside your advertised list; they are now "
            "available for the rest of this session. Call them directly by name."
        )
    else:
        note = (
            "These tools are NOT in your advertised tool list, but Nexus will "
            "accept calls to them. Call directly by name using these schemas."
        )
    out: dict[str, Any] = {
        "ok": True,
        "mode": _MODE,
        "tools": [_spec_json(selected[n]) for n in discovered],
        "note": note,
    }
    if already_advertised:
        out["already_advertised"] = already_advertised
    return json.dumps(out, ensure_ascii=False)


# ── prompt catalog ───────────────────────────────────────────────────────────


def catalog_markdown() -> str:
    """Stable-zone prompt section listing every non-core tool.

    Lists *all* non-core tools regardless of stickiness, so the section only
    changes when the registry itself changes (MCP connect/disconnect — which
    already invalidates the measured overhead). Returns "" when discovery is
    off or there is nothing to list.
    """
    reg = _INSTALLED_REGISTRY
    if reg is None:
        return ""
    handlers = getattr(reg, "_handlers", {}) or {}
    hidden = sorted(n for n in handlers if n not in _CORE and n != "tool_search")
    if not hidden:
        return ""
    lines = [
        "## More tools (on demand)",
        "",
        ("Only your core tools are in the tool schema block above. Everything "
         "below is still callable: call `tool_search(query=\"...\")` (or "
         "`tool_search(names=[\"...\"])`) to receive its full schema, then call "
         "the tool by name."),
        "",
    ]
    for n in hidden:
        spec = handlers[n].tool
        lines.append(f"- `{n}` — {_blurb(getattr(spec, 'description', '') or '')}")
    return "\n".join(lines)


def discovery_stats() -> dict[str, Any]:
    """Counters for /context-stats and logging (Phase 5 telemetry)."""
    reg = _INSTALLED_REGISTRY
    handlers = getattr(reg, "_handlers", {}) or {} if reg else {}
    return {
        "installed": _INSTALLED_REGISTRY is not None,
        "mode": _MODE,
        "core_count": len(_CORE),
        "hidden_count": max(0, len(handlers) - len(_advertised_names())) if reg else 0,
        "sticky_sessions": len(_STICKY),
        "sticky_tools_current_session": len(_STICKY.get(_current_session_id(), {})),
    }


# ── install ──────────────────────────────────────────────────────────────────


def install_tool_discovery(registry: Any) -> bool:
    """Filter ``registry.specs()`` to the advertised set and register ``tool_search``.

    Called from ``build_tool_registry`` after every handler is registered and
    (for restricted registries) after the allowlist applied. Returns True when
    installed; False when disabled by config or not applicable.

    The patch follows the same monkey-patch style the registry already uses
    for ``dispatch``: loom calls ``specs()`` live on every request
    (``loom/loop/_executor.py``), so filtering here controls exactly what the
    provider sees per call — and the overhead measurement in ``_builder``
    automatically measures the advertised set, not the full registry.
    """
    global _INSTALLED_REGISTRY, _MODE, _CORE, _STICKY_MAX

    try:
        from ..config_file import load_cached

        agent_cfg = getattr(load_cached(), "agent", None)
    except Exception:  # noqa: BLE001 — config read failure = disabled
        log.debug("tool discovery: config unreadable; not installing", exc_info=True)
        return False
    if not agent_cfg or not bool(getattr(agent_cfg, "tool_discovery", False)):
        return False

    mode = str(getattr(agent_cfg, "tool_advertisement", "sticky") or "sticky")
    if mode not in ("sticky", "pure"):
        mode = "sticky"
    if mode == "pure":
        # Visibility, not a blocker: pure mode bets on pass-through of
        # undeclared tool calls, which Anthropic does and some
        # OpenAI-compat servers with constrained decoding do not.
        log.warning(
            "tool discovery: pure advertisement mode enabled — discovered tool "
            "schemas are only delivered via tool_search results. If your provider "
            "constrains generation to declared tools, use tool_advertisement='sticky'."
        )

    # Core override: exact names must exist in the registry, else warn+drop
    # (mirrors _apply_tool_allowlist's behavior for unknown names).
    override = [str(n) for n in (getattr(agent_cfg, "core_tools", None) or [])]
    if override:
        handlers = getattr(registry, "_handlers", {}) or {}
        valid = [n for n in override if n in handlers]
        missing = sorted(set(override) - set(valid))
        if missing:
            log.warning("tool discovery: core_tools lists unknown tools, dropped: %s", missing)
        valid.append("tool_search")
        _CORE = frozenset(valid)
    else:
        # Keep defaults that actually exist (conditional tools may be absent).
        _CORE = frozenset(n for n in _DEFAULT_CORE if n in (getattr(registry, "_handlers", {}) or {})) | {"tool_search"}

    try:
        _STICKY_MAX = max(16, int(getattr(agent_cfg, "tool_discovery_sticky_max", 28) or 28))
    except (TypeError, ValueError):
        _STICKY_MAX = 28

    _INSTALLED_REGISTRY = registry
    _MODE = mode
    registry.register(_ToolSearchHandler())

    def _patched_specs() -> list[Any]:
        allowed = _advertised_names()
        return [h.tool for name, h in registry._handlers.items() if name in allowed]

    try:
        registry.specs = _patched_specs  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001 — never leave the registry unusable
        log.warning("tool discovery: could not patch specs(); not installing", exc_info=True)
        registry.unregister("tool_search")
        _INSTALLED_REGISTRY = None
        return False

    advertised_n = len(_advertised_names())
    log.info(
        "tool discovery installed: %d core + %d hidden tools, mode=%s, sticky_max=%d",
        advertised_n, len(registry._handlers) - advertised_n, mode, _STICKY_MAX,
    )
    return True
