from __future__ import annotations

import json
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
    path = resolve_templates(step.file_save_path or "", ctx)
    content = resolve_templates(step.file_save_content or "", ctx)
    mode = step.file_save_mode or "overwrite"

    if not path:
        raise ValueError(f"step '{step.name}' missing file_save_path")

    expanded = os.path.expanduser(path)
    parent = os.path.dirname(expanded)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)

    open_mode = "a" if mode == "append" else "w"
    with open(expanded, open_mode, encoding="utf-8") as f:
        written = f.write(content)

    return {"path": expanded, "bytes_written": written, "mode": mode}
