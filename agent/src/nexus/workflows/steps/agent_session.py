from __future__ import annotations

from typing import Any

from ..expressions import resolve_templates
from ..models import StepConfig
from ._helpers import _build_json_instruction, _parse_llm_output


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    from ...agent.loop.agent import Agent
    from ...agent.llm import ChatMessage as NxChatMessage
    from ...agent.context import CURRENT_SESSION_ID
    from ...server.session_store.store import SessionStore

    prompt = resolve_templates(step.prompt or "", ctx)
    if not prompt:
        raise ValueError(f"step '{step.name}' missing prompt for agent session")

    agent: Agent | None = getattr(engine, "_agent", None)
    store: SessionStore | None = getattr(engine, "_sessions", None)

    if agent is None or store is None:
        return {"result": prompt, "_simulated": True}

    session = store.create()
    store.mark_hidden(session.id)
    session_id = session.id

    force_json = step.output_format == "json"
    if force_json:
        schema_str = resolve_templates(step.output_schema, ctx) if step.output_schema else None
        agent_prompt = _build_json_instruction(schema_str) + prompt
    else:
        agent_prompt = prompt

    resolved_model = step.model or None
    if not resolved_model:
        try:
            from ...config_file import load as load_config

            resolved_model = getattr(load_config().agent, "default_model", None) or None
        except Exception:
            pass

    import logging

    logging.getLogger(__name__).warning(
        "workflow agent_session: step=%r model=%r session=%s",
        step.name,
        resolved_model,
        session_id,
    )

    final_text = ""
    final_messages: list[NxChatMessage] = []

    _sid_token = CURRENT_SESSION_ID.set(session_id)
    try:
        async for event in agent.run_turn_stream(
            agent_prompt,
            history=[],
            session_id=session_id,
            model_id=resolved_model,
        ):
            etype = event.get("type")
            if etype == "delta":
                final_text += event.get("text", "")
            elif etype == "error":
                raise RuntimeError(event.get("message", "agent error"))
            elif etype == "done":
                raw_msgs = event.get("messages") or []
                final_messages = [
                    m if isinstance(m, NxChatMessage) else NxChatMessage(**m)
                    for m in raw_msgs
                ]
    finally:
        CURRENT_SESSION_ID.reset(_sid_token)

    if final_messages:
        store.replace_history(session_id, final_messages)

    output: dict[str, Any] = {"session_id": session_id}
    parsed = _parse_llm_output(final_text, force_json)

    if isinstance(parsed, dict):
        output.update(parsed)
    elif parsed is not None:
        output["result"] = parsed
    else:
        output["result"] = final_text.strip()

    return output
