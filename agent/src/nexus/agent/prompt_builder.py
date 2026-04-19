"""System prompt builder with progressive disclosure and SKILLS_GUIDANCE."""

from __future__ import annotations

from ..skills.registry import SkillRegistry

DEFAULT_IDENTITY = (
    "You are Nexus, a self-improving agent. You can execute tasks, "
    "use and create skills, and call external tools. "
    "Prefer concise, factual replies."
)

SKILLS_GUIDANCE = (
    "When you complete a non-trivial task (5+ tool calls, tricky error recovery, "
    "user correction, or a workflow you'd want to repeat), persist it by calling "
    "`skill_manage` with action=create. If you use a skill and find it stale or "
    "wrong, patch it via action=patch in the same turn."
)


def build_system_prompt(
    registry: SkillRegistry,
    *,
    context: str | None = None,
) -> str:
    parts = [DEFAULT_IDENTITY, "", SKILLS_GUIDANCE, ""]

    if context:
        parts.append(f"Session context: {context}")
        parts.append("")

    descs = registry.descriptions()
    if descs:
        parts.append("Available skills (name — description):")
        for name, desc in descs:
            parts.append(f"  - {name} — {desc}")
        parts.append(
            "\nCall `skill_view(name)` to load a skill's full instructions before following them."
        )
    else:
        parts.append("No skills are currently loaded.")

    return "\n".join(parts)
