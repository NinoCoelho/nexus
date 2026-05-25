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
    await asyncio.sleep(step.duration_seconds)
    return {"waited_seconds": step.duration_seconds}
