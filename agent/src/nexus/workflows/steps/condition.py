from __future__ import annotations

from typing import Any

from ..expressions import evaluate_condition
from ..models import StepConfig


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    return {"result": evaluate_condition(step.expression or "", ctx)}
