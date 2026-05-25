from __future__ import annotations

import json
import re
from typing import Any

from ..expressions import resolve_templates
from ..models import StepConfig
from ._helpers import _build_json_instruction, _parse_llm_output


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    if step.output_format == "llm":
        return await _execute_llm_transform(engine, step, ctx)
    elif step.output_format == "script":
        return await _execute_script_transform(step, ctx)
    else:
        template = resolve_templates(step.template or "", ctx)
        if step.output_format == "json":
            try:
                return json.loads(template)
            except json.JSONDecodeError:
                return {"raw": template}
        return {"result": template}


async def _execute_llm_transform(
    engine: Any, step: StepConfig, ctx: dict[str, Any],
) -> Any:
    system_prompt = step.tool or "Transform the following input as instructed. Output only the result."
    user_input = resolve_templates(step.template or "", ctx)
    if not user_input:
        raise ValueError(f"step '{step.name}' missing input for LLM transform")

    if getattr(engine, "_agent", None) is None or getattr(engine, "_sessions", None) is None:
        return {"result": user_input, "_simulated": True}

    force_json = step.output_format == "json"
    prompt = system_prompt + "\n\n" + user_input
    if force_json:
        schema_str = resolve_templates(step.output_schema, ctx) if step.output_schema else None
        prompt = _build_json_instruction(schema_str) + prompt

    from ..engine import _single_shot_llm

    final_text, _ = await _single_shot_llm(
        engine, prompt, model_id=step.model or None,
    )

    parsed = _parse_llm_output(final_text, force_json)
    if isinstance(parsed, str):
        return {"result": parsed}
    if parsed is not None:
        return parsed
    return {"result": final_text.strip()}


async def _execute_script_transform(step: StepConfig, ctx: dict[str, Any]) -> Any:
    template = resolve_templates(step.template or "", ctx)
    if not template:
        raise ValueError(f"step '{step.name}': missing script")

    data = ctx.get("steps", {})
    safe_builtins = {
        "__import__": __import__,
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
        "enumerate": enumerate, "filter": filter, "float": float, "int": int,
        "isinstance": isinstance, "len": len, "list": list, "map": map,
        "max": max, "min": min, "None": None, "print": print, "range": range,
        "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum,
        "tuple": tuple, "type": type, "zip": zip, "True": True, "False": False,
    }
    global_ns: dict[str, Any] = {"__builtins__": safe_builtins, "json": json, "re": re}
    local_vars: dict[str, Any] = {"data": data, "result": None}
    try:
        exec(template, global_ns, local_vars)
        return {"result": local_vars.get("result")}
    except Exception as exc:
        raise ValueError(f"script error in step '{step.name}': {exc}") from exc
