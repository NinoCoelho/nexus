"""Visualize tool: reads a vault data-table and returns an inline nexus-chart block.

The agent drops the returned fenced block into its reply and the UI renders
it as a chart via ChartBlock.tsx.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from ..agent.llm import ToolSpec

VISUALIZE_TABLE_TOOL = ToolSpec(
    name="visualize_table",
    description=(
        "Read a vault data-table and return a ready-to-paste ```nexus-chart``` fenced "
        "block that renders inline in chat as a bar, line, or pie chart. "
        "For ad-hoc data the agent can write the fence directly without this tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative path to the data-table .md file.",
            },
            "chart_type": {
                "type": "string",
                "enum": ["bar", "line", "pie"],
                "description": "Chart type. Default: 'bar'.",
            },
            "x": {
                "type": "string",
                "description": "Name of the field to use as the X axis / slice label.",
            },
            "y": {
                "type": "string",
                "description": (
                    "Name of the numeric field to plot, OR one of the aggregation "
                    "keywords: 'count' (count rows per x value), "
                    "'sum:<field>' (sum a numeric field), 'avg:<field>' (average)."
                ),
            },
            "title": {
                "type": "string",
                "description": "Optional chart title.",
            },
        },
        "required": ["path", "x", "y"],
    },
)


def handle_visualize_tool(args: dict[str, Any]) -> str:
    from .. import vault_datatable

    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "`path` is required"})
    x_field = args.get("x", "")
    y_spec = args.get("y", "")
    if not x_field or not y_spec:
        return json.dumps({"ok": False, "error": "`x` and `y` are required"})

    chart_type = args.get("chart_type", "bar")
    title = args.get("title", "")

    try:
        tbl = vault_datatable.read_table(path)
    except (FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})

    rows = tbl.get("rows", [])
    if not rows:
        return json.dumps({"ok": False, "error": "table has no rows"})

    # Aggregate
    data = _aggregate(rows, x_field, y_spec)
    if isinstance(data, str):
        return json.dumps({"ok": False, "error": data})

    chart_spec: dict[str, Any] = {
        "type": chart_type,
        "x": x_field,
        "y": y_spec,
        "data": data,
    }
    if title:
        chart_spec["title"] = title

    import yaml
    chart_yaml = yaml.dump(chart_spec, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    fence = f"```nexus-chart\n{chart_yaml}\n```"
    return fence


def _aggregate(
    rows: list[dict[str, Any]], x_field: str, y_spec: str
) -> list[dict[str, Any]] | str:
    """Aggregate rows and return [{x_value, y_value}] or an error string."""
    if y_spec == "count":
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            key = str(r.get(x_field, ""))
            counts[key] += 1
        return [{"x": k, "y": v} for k, v in counts.items()]

    if y_spec.startswith("sum:") or y_spec.startswith("avg:"):
        agg_type, target_field = y_spec.split(":", 1)
        buckets: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            key = str(r.get(x_field, ""))
            try:
                val = float(r.get(target_field, 0) or 0)
            except (TypeError, ValueError):
                val = 0.0
            buckets[key].append(val)
        result = []
        for k, vals in buckets.items():
            y_val = sum(vals) if agg_type == "sum" else (sum(vals) / len(vals) if vals else 0.0)
            result.append({"x": k, "y": round(y_val, 4)})
        return result

    # y_spec is a direct field name — collect (x, y) pairs
    pairs = []
    for r in rows:
        x_val = str(r.get(x_field, ""))
        raw_y = r.get(y_spec)
        try:
            y_val = float(raw_y) if raw_y is not None else 0.0
        except (TypeError, ValueError):
            y_val = 0.0
        pairs.append({"x": x_val, "y": y_val})
    return pairs
