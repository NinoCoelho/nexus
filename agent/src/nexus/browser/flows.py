"""Persistent action-flow replay — ``save_flow`` / ``replay`` / ``flows``.

A repeatable browser sequence (the agent's own successful steps) is saved
under a name and re-executed later in ONE tool call — even in a fresh
session, even after a daemon restart. This collapses "do what we did
yesterday" from N model round-trips to one.

Storage: ``~/.nexus/browser/flows/<sanitized-name>.json`` —
``{name, surface: ext|cdp, created, steps: [{action, args, needs_value?}]}``.

Safety:
- Typed values (``type``/``fill_login``) are NEVER persisted — steps are
  saved with ``needs_value: true`` and replay aborts on them with a clear
  error instead of silently replaying a redacted secret.
- Replay executes through the same invoke machinery as a manual call, so
  every per-action gate (panel Allow/Deny on navigate/js, HITL) applies.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# Actions that only perceive (no state change) — dropped from saved flows
# by default; replaying them would cost round-trips for nothing.
PERCEPTION_ACTIONS = frozenset(
    {
        "read",
        "dom_text",
        "extract",
        "find_element",
        "inspect_element",
        "viewport_info",
        "page_signature",
        "interactive_map",
        "ax_tree",
        "screenshot",
        "element_screenshot",
        "info",
        "list_tabs",
        "transcript",
    }
)
# Meta actions that must never be recorded as flow steps.
META_ACTIONS = frozenset({"save_flow", "replay", "flows"})
# Typed-value actions — the value is redacted before persisting.
SECRET_ARG_ACTIONS = frozenset({"type", "fill_login"})
_SECRET_ARGS = ("text", "password", "value")


def flows_dir() -> Path:
    d = Path.home() / ".nexus" / "browser" / "flows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize(name: str) -> str:
    seg = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return seg[:80] or "flow"


def _redact_step(step: dict[str, Any]) -> dict[str, Any]:
    action = step.get("action")
    if action in SECRET_ARG_ACTIONS:
        args = dict(step.get("args") or {})
        touched = False
        for k in _SECRET_ARGS:
            if args.get(k) is not None:
                args[k] = None
                touched = True
        if touched:
            return {"action": action, "args": args, "needs_value": True}
    return {"action": action, "args": dict(step.get("args") or {})}


def save_flow(name: str, surface: str, steps: list[dict[str, Any]]) -> Path:
    """Persist ``steps`` (``[{action, args}]``) under ``name`` for ``surface``.

    Perception actions are dropped and typed values redacted before the
    file ever touches disk.
    """
    if not name or not str(name).strip():
        raise ValueError("flow name is required")
    clean: list[dict[str, Any]] = []
    for s in steps:
        if not isinstance(s, dict) or s.get("action") in META_ACTIONS:
            continue
        if s.get("action") in PERCEPTION_ACTIONS:
            continue
        clean.append(_redact_step(s))
    path = flows_dir() / f"{_sanitize(str(name))}.json"
    path.write_text(
        json.dumps(
            {
                "name": str(name).strip(),
                "surface": surface,
                "created": time.time(),
                "steps": clean,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return path


def load_flow(name: str) -> dict[str, Any] | None:
    try:
        return json.loads((flows_dir() / f"{_sanitize(str(name))}.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def list_flows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(flows_dir().glob("*.json")):
        try:
            f = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "name": f.get("name") or p.stem,
                "surface": f.get("surface"),
                "steps": len(f.get("steps") or []),
                "created": f.get("created"),
            }
        )
    return out
