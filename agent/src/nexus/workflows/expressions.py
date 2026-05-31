"""Template expression resolution for workflow steps.

Resolves ``{{trigger.x}}``, ``{{steps.id.output}}``, ``{{vars.x}}``,
``{{now}}``, ``{{date}}``, ``{{uuid}}``, ``{{timestamp}}``
expressions in step configurations. Uses a safe recursive resolver that
supports dotted path access into dicts — no arbitrary code execution.
"""

from __future__ import annotations

import datetime as _dt
import logging as _logging
import re
import uuid as _uuid
from typing import Any

_EXPR_RE = re.compile(r"\{\{(.+?)\}\}")

_log = _logging.getLogger(__name__)


def slugify(name: str) -> str:
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    parts = [p for p in re.split(r"[\s_\-]+", name) if p]
    if not parts:
        return ""
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def _resolve_path(obj: Any, path: str) -> Any:
    parts = [p for p in path.strip().split(".") if p]
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def resolve_templates(
    value: Any,
    context: dict[str, Any],
    *,
    _unresolved: list[str] | None = None,
) -> Any:
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            resolved = _resolve_path(context, m.group(1))
            if resolved is None:
                if _unresolved is not None:
                    _unresolved.append(m.group(1).strip())
                return m.group(0)
            return str(resolved)
        return _EXPR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: resolve_templates(v, context, _unresolved=_unresolved) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, context, _unresolved=_unresolved) for item in value]
    return value


def build_context(
    trigger_payload: dict[str, Any],
    step_outputs: dict[str, Any],
    variables: dict[str, str],
    slug_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    id_to_slug: dict[str, str] = {}
    if slug_map:
        for slug, step_id in slug_map.items():
            id_to_slug[step_id] = slug
    steps: dict[str, Any] = {}
    for step_id, val in step_outputs.items():
        key = id_to_slug.get(step_id, step_id)
        steps[key] = val
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "trigger": trigger_payload,
        "steps": steps,
        "vars": variables,
        "now": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "uuid": str(_uuid.uuid4()),
        "timestamp": str(int(now.timestamp())),
    }


class _AttrDict(dict):
    def __getattr__(self, key: str) -> Any:
        try:
            v = self[key]
            if isinstance(v, dict):
                return _AttrDict(v)
            return v
        except KeyError:
            raise AttributeError(key)


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    resolved = resolve_templates(expression, context)
    if resolved != expression:
        try:
            result = bool(eval(str(resolved), {"__builtins__": {}}, {}))
            _log.info("condition resolved: %r → %r = %s", expression, resolved, result)
            return result
        except Exception as exc:
            fallback = bool(resolved) and str(resolved) not in ("False", "false", "0", "", "None", "none")
            _log.warning("condition eval failed: %r → %r (%s), falling back to truthiness=%s", expression, resolved, exc, fallback)
            return fallback
    try:
        namespace: dict[str, Any] = {}
        for k, v in context.items():
            namespace[k] = _AttrDict(v) if isinstance(v, dict) else v
        namespace["true"] = True
        namespace["false"] = False
        namespace["none"] = None
        namespace["null"] = None
        result = bool(eval(expression, {"__builtins__": {}}, namespace))
        _log.info("condition (no template vars): %r = %s", expression, result)
        return result
    except Exception as exc:
        _log.warning("condition eval failed (no template vars): %r (%s), defaulting to False", expression, exc)
        return False
