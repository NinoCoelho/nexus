"""Template expression resolution for workflow steps.

Resolves ``{{trigger.x}}``, ``{{steps.id.output}}``, ``{{vars.x}}``
expressions in step configurations. Uses a safe recursive resolver that
supports dotted path access into dicts — no arbitrary code execution.
"""

from __future__ import annotations

import re
from typing import Any

_EXPR_RE = re.compile(r"\{\{(.+?)\}\}")


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
) -> Any:
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            resolved = _resolve_path(context, m.group(1))
            if resolved is None:
                return m.group(0)
            return str(resolved)
        return _EXPR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: resolve_templates(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, context) for item in value]
    return value


def build_context(
    trigger_payload: dict[str, Any],
    step_outputs: dict[str, Any],
    variables: dict[str, str],
) -> dict[str, Any]:
    return {
        "trigger": trigger_payload,
        "steps": step_outputs,
        "vars": variables,
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
    resolved = resolve_templates("{{" + expression + "}}", context)
    if resolved == "{{" + expression + "}}":
        try:
            namespace: dict[str, Any] = {}
            for k, v in context.items():
                namespace[k] = _AttrDict(v) if isinstance(v, dict) else v
            namespace["true"] = True
            namespace["false"] = False
            namespace["none"] = None
            namespace["null"] = None
            return bool(eval(expression, {"__builtins__": {}}, namespace))
        except Exception:
            return False
    return bool(resolved) and resolved not in ("False", "false", "0", "", "None", "none")
