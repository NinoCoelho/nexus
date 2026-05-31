from __future__ import annotations

import os
from typing import Any

from ..expressions import resolve_templates
from ..models import StepConfig


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    path = resolve_templates(step.file_read_path or "", ctx)
    if not path:
        raise ValueError(f"step '{step.name}' missing file_read_path")

    expanded = os.path.expanduser(path)
    if not os.path.isfile(expanded):
        raise FileNotFoundError(f"file not found: {expanded}")

    size = os.path.getsize(expanded)
    with open(expanded, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return {"content": content, "path": expanded, "size": size}
