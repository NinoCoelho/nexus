"""Internal state tools: skills_list and skill_view.

``skill_view`` is also the central choke point for skill credential
prompts. Skills declare ``requires_keys`` in frontmatter; when the
agent fetches the body, missing keys (neither in env nor in
``~/.nexus/secrets.toml``) trigger a masked HITL form. Stored values
are then available to ``$NAME`` substitution at the tool boundary —
the body itself is returned with placeholders intact, so the LLM
never sees the raw values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..agent.llm import ToolSpec
from ..skills.registry import SkillRegistry

if TYPE_CHECKING:
    from ..agent.ask_user_tool import AskUserHandler

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
    def __init__(
        self,
        registry: SkillRegistry,
        ask_user: AskUserHandler | None = None,
    ) -> None:
        self._registry = registry
        self._ask_user = ask_user

    def set_ask_user(self, ask_user: AskUserHandler) -> None:
        """Late-bound by app.py once the handler is constructed."""
        self._ask_user = ask_user

    async def invoke(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if tool_name == "skills_list":
            return self._skills_list()
        if tool_name == "skill_view":
            return await self._skill_view(args)
        return ToolResult(ok=False, data=None, error=f"unknown tool: {tool_name!r}")

    def _skills_list(self) -> ToolResult:
        entries = [
            {"name": s.name, "description": s.description, "trust": s.trust}
            for s in self._registry.list()
        ]
        return ToolResult(ok=True, data={"skills": entries, "count": len(entries)})

    async def _skill_view(self, args: dict[str, Any]) -> ToolResult:
        from .. import secrets

        name = args.get("name")
        if not isinstance(name, str) or not name:
            return ToolResult(ok=False, data=None, error="`name` is required")
        try:
            skill = self._registry.get(name)
        except KeyError:
            return ToolResult(ok=False, data=None, error=f"no such skill: {name!r}")

        missing = [req for req in skill.requires_keys if not secrets.exists(req.name)]
        if missing:
            prompted = await self._prompt_for_missing_keys(skill.name, missing)
            if not prompted.ok:
                return prompted

        data: dict[str, Any] = {"name": skill.name, "body": skill.body}
        # Tell the LLM how to consume credentials this skill declared. Without
        # this hint, the model falls back to its trained behavior of asking
        # the user to export an env var or paste the key — even though we
        # already collected it. The values themselves are NOT included; the
        # placeholder is everything the model needs.
        if skill.requires_keys:
            data["credentials"] = {
                "available": [req.name for req in skill.requires_keys],
                "usage": (
                    "These credentials are stored. Use them as `$NAME` "
                    "placeholders directly in `http_call` args (headers, body, "
                    "or URL) — the server substitutes the real value at the "
                    "tool boundary. The LLM never sees the raw value; that is "
                    "by design. Do NOT ask the user for the key, do NOT try "
                    "to read it via `terminal` (echo/printenv won't see it), "
                    "and do NOT include the literal value anywhere in your "
                    "messages. Just write `$NAME` in the tool args."
                ),
            }
        return ToolResult(ok=True, data=data)

    async def _prompt_for_missing_keys(
        self, skill_name: str, missing: list[Any]
    ) -> ToolResult:
        """Open a masked form for missing keys; persist answers to secrets.toml.

        Returns ``ok=True`` on success (or when ask_user is unavailable, so the
        body is still returned and the agent can decide how to proceed).
        Returns ``ok=False`` with a clear error when the user dismissed or the
        request timed out — the body is then withheld so the agent reports the
        gap rather than running half-configured.
        """
        from .. import secrets
        from ..agent.ask_user_tool import parse_parked_sentinel

        if self._ask_user is None:
            # No HITL channel (e.g. CLI invocation). Surface the gap clearly
            # rather than silently returning a half-functional body.
            names = ", ".join(req.name for req in missing)
            return ToolResult(
                ok=False,
                data=None,
                error=(
                    f"skill {skill_name!r} requires credentials "
                    f"({names}) and no HITL channel is available to prompt for them. "
                    f"Set them via Settings → Credentials, or as environment variables."
                ),
            )

        fields = []
        for req in missing:
            field: dict[str, Any] = {
                "name": req.name,
                "label": req.name,
                "kind": "text",
                "required": True,
                "secret": True,
                "help": req.help or f"Required by skill {skill_name!r}.",
            }
            if req.url:
                field["help_url"] = req.url
            fields.append(field)

        result = await self._ask_user.invoke(
            {
                "prompt": f"Credentials required for skill: {skill_name}",
                "kind": "form",
                "fields": fields,
                "title": f"Credentials required for skill: {skill_name}",
                "description": (
                    "These values are stored locally in ~/.nexus/secrets.toml "
                    "(file mode 0600). The LLM never sees the raw values — they "
                    "are substituted into outgoing tool calls via $NAME placeholders."
                ),
            }
        )

        if not result.ok:
            return ToolResult(
                ok=False,
                data=None,
                error=result.error or "ask_user failed",
            )
        if result.timed_out:
            return ToolResult(
                ok=False,
                data=None,
                error=f"timed out waiting for credentials for skill {skill_name!r}",
            )
        if isinstance(result.answer, str) and parse_parked_sentinel(result.answer):
            # The form was parked. The agent loop will end the turn and the user
            # will resume later. Surface a sentinel-ish error so the model knows
            # not to proceed in this turn.
            return ToolResult(
                ok=False,
                data=None,
                error=(
                    f"credential prompt parked for skill {skill_name!r}; "
                    "session will resume once the user submits the form"
                ),
            )

        answer = result.answer
        if not isinstance(answer, dict):
            return ToolResult(
                ok=False,
                data=None,
                error="credential form did not return a dict answer",
            )

        for req in missing:
            value = answer.get(req.name)
            if not isinstance(value, str) or not value:
                return ToolResult(
                    ok=False,
                    data=None,
                    error=f"missing value for credential {req.name!r}",
                )
            secrets.set(req.name, value, kind="skill", skill=skill_name)

        return ToolResult(ok=True, data=None)
