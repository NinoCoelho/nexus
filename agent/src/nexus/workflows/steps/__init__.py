from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..models import StepType
from . import agent_session, condition, delay, http_request, mcp_call, tool_call, transform

ExecuteFn = Callable[..., Awaitable[Any]]

STEP_REGISTRY: dict[StepType, ExecuteFn] = {
    StepType.tool_call: tool_call.execute_step,
    StepType.agent_session: agent_session.execute_step,
    StepType.mcp_call: mcp_call.execute_step,
    StepType.http_request: http_request.execute_step,
    StepType.transform: transform.execute_step,
    StepType.condition: condition.execute_step,
    StepType.delay: delay.execute_step,
}
