from __future__ import annotations

import re
from typing import Any


def _build_json_instruction(schema: str | None = None) -> str:
    parts = [
        "You MUST respond with ONLY valid JSON — no markdown fences, "
        "no commentary, no explanation. Output a single JSON object "
        "and nothing else.",
    ]
    if schema:
        parts.append(
            f"\nRespond with a JSON object matching this structure:\n"
            f"{schema}\n"
            f"Replace placeholder values with actual data."
        )
    return "".join(parts) + "\n\n"


def _parse_llm_output(text: str, force_json: bool) -> Any:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    if force_json:
        import json

        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return stripped
    else:
        if stripped.startswith("{") or stripped.startswith("["):
            import json

            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
        return None
