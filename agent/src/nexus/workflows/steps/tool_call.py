from __future__ import annotations

import asyncio
from typing import Any

from ..models import StepConfig


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    from ...agent._loom_bridge.registry import build_tool_registry

    tool_name = step.tool
    if not tool_name:
        raise ValueError(f"step '{step.name}' missing tool name")

    registry = build_tool_registry()
    handler = registry.get(tool_name)
    if handler is None:
        raise ValueError(f"unknown tool: {tool_name}")

    args = resolved_input or {}
    if asyncio.iscoroutinefunction(handler):
        result = await handler(**args)
    else:
        result = handler(**args)
    if isinstance(result, dict):
        return result
    return {"result": result}
