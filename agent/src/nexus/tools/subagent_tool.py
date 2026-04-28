"""spawn_subagents agent tool.

Runs one or more sub-agents in parallel with fresh, isolated contexts. Each
sub-agent gets the full tool registry except HITL (``ask_user`` / ``terminal``)
and recursive ``spawn_subagents``. Returns only each sub-agent's final answer
so the parent's context grows by N short answers instead of N full transcripts.

The runner itself is constructed and late-bound on ``AgentHandlers`` by
``server/app.py``; this module owns only the tool spec and the JSON-shaped
handler that delegates into the runner.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

# Hard cap on nesting. Keeps a runaway sub-agent from spinning up its own
# fan-out and exploding the wall-clock / token cost. v1 is 1: parent can
# spawn sub-agents, sub-agents cannot spawn further.
MAX_SUBAGENT_DEPTH = 1

# Hard cap on parallel fan-out per call. Keeps the LLM from accidentally
# spawning a swarm; deep research with more than 8 angles probably wants a
# different decomposition strategy anyway.
MAX_TASKS_PER_CALL = 8


SPAWN_SUBAGENTS_TOOL = ToolSpec(
    name="spawn_subagents",
    description=(
        "Run one or more sub-agents in parallel with fresh, isolated contexts. "
        "Each sub-agent gets the full tool registry except ask_user/terminal "
        "and cannot recursively spawn further sub-agents. Returns only each "
        "sub-agent's final answer — their tool calls and intermediate output "
        "do NOT pollute your context window. Use this for deep research, "
        "parallel investigation across angles, or any task where you want to "
        "delegate work and consume only the conclusion. Each task's `prompt` "
        "must be self-contained: the sub-agent has no memory of this "
        f"conversation. Maximum {MAX_TASKS_PER_CALL} tasks per call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Short label for this sub-task (logged, shown "
                                "in the parent's tool result for synthesis)."
                            ),
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Self-contained instruction for the sub-agent. "
                                "Include all relevant scope/context — the "
                                "sub-agent has no memory of this conversation."
                            ),
                        },
                        "model_id": {
                            "type": "string",
                            "description": (
                                "Optional model override for this sub-agent. "
                                "Defaults to the parent's configured model."
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
                "minItems": 1,
                "maxItems": MAX_TASKS_PER_CALL,
            },
        },
        "required": ["tasks"],
    },
)


async def handle_spawn_subagents(
    args: dict[str, Any],
    *,
    runner: Any,
    parent_session_id: str | None,
    depth: int,
) -> str:
    """Tool handler. Returns a JSON string for the LLM tool result channel.

    ``runner`` is the late-bound async callable on ``AgentHandlers``. It is
    None inside a sub-agent's restricted registry so recursive spawning is
    refused at the handler boundary (a defence in depth alongside
    ``MAX_SUBAGENT_DEPTH``).
    """
    if runner is None:
        return json.dumps({
            "ok": False,
            "error": (
                "spawn_subagents unavailable: runner not wired (sub-agents "
                "cannot spawn further sub-agents in v1)"
            ),
        })
    if parent_session_id is None:
        return json.dumps({
            "ok": False,
            "error": "spawn_subagents requires an active session context",
        })
    if depth >= MAX_SUBAGENT_DEPTH:
        return json.dumps({
            "ok": False,
            "error": f"spawn_subagents depth limit reached (max {MAX_SUBAGENT_DEPTH})",
        })

    tasks = args.get("tasks") or []
    if not isinstance(tasks, list) or not tasks:
        return json.dumps({"ok": False, "error": "`tasks` must be a non-empty array"})
    if len(tasks) > MAX_TASKS_PER_CALL:
        return json.dumps({
            "ok": False,
            "error": f"too many tasks ({len(tasks)}); max {MAX_TASKS_PER_CALL} per call",
        })
    for i, t in enumerate(tasks):
        if not isinstance(t, dict) or not isinstance(t.get("prompt"), str) or not t["prompt"].strip():
            return json.dumps({
                "ok": False,
                "error": f"task[{i}] missing or empty `prompt`",
            })

    try:
        results = await runner(tasks, parent_session_id=parent_session_id, depth=depth)
    except Exception as exc:  # pragma: no cover — defensive
        return json.dumps({"ok": False, "error": f"subagent runner crashed: {exc!r}"})

    out = []
    for t, r in zip(tasks, results):
        out.append({
            "name": t.get("name"),
            "session_id": r.get("session_id"),
            "result": r.get("result", ""),
            "error": r.get("error"),
        })
    return json.dumps({"ok": True, "results": out})
