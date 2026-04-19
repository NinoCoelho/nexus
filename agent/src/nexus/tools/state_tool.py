"""Internal state tools: skills_list and skill_view."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..agent.llm import ToolSpec
from ..skills.registry import SkillRegistry

SKILLS_LIST_TOOL = ToolSpec(
    name="skills_list",
    description="List all installed skills with name, description, and trust tier.",
    parameters={"type": "object", "properties": {}},
)

SKILL_VIEW_TOOL = ToolSpec(
    name="skill_view",
    description="Fetch the full SKILL.md body for a named skill.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name."},
        },
        "required": ["name"],
    },
)

STATE_TOOLS = [SKILLS_LIST_TOOL, SKILL_VIEW_TOOL]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any
    error: str | None = None

    def to_text(self) -> str:
        return json.dumps({"ok": self.ok, "data": self.data, "error": self.error}, ensure_ascii=False)


class StateToolHandler:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def invoke(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if tool_name == "skills_list":
            return self._skills_list()
        if tool_name == "skill_view":
            return self._skill_view(args)
        return ToolResult(ok=False, data=None, error=f"unknown tool: {tool_name!r}")

    def _skills_list(self) -> ToolResult:
        entries = [
            {"name": s.name, "description": s.description, "trust": s.trust}
            for s in self._registry.list()
        ]
        return ToolResult(ok=True, data={"skills": entries, "count": len(entries)})

    def _skill_view(self, args: dict[str, Any]) -> ToolResult:
        name = args.get("name")
        if not isinstance(name, str) or not name:
            return ToolResult(ok=False, data=None, error="`name` is required")
        try:
            skill = self._registry.get(name)
        except KeyError:
            return ToolResult(ok=False, data=None, error=f"no such skill: {name!r}")
        return ToolResult(ok=True, data={"name": skill.name, "body": skill.body})
