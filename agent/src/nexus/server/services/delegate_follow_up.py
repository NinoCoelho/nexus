"""Delegate-job completion hook: session event + follow-up report turn.

Wired once in ``app.py`` via :func:`wire_delegate_follow_up` and late-bound
into ``AgentHandlers.delegate_on_complete``; ``agent_delegate`` (async mode)
awaits the returned callback when a job reaches a terminal state.

What the callback does, gated by ``[delegate].on_complete`` (default
``"report"``):

1. Publish a ``delegate_done`` event on the originating session's bus —
   the UI can surface a completion banner with status/exit code.
2. Chain a follow-up turn so the agent reads the result and reports in
   chat **without the user having to ask**:
   - a turn already running → ``runner.enqueue`` (race-free: either
     injected at the next tool-batch boundary or chained after the turn),
   - session idle → start a detached ``ChatTurnRunner`` report turn, same
     recipe as ``POST /chat/stream``.

``"silent"`` keeps the old poll-only behavior (no event, no turn).
"""

from __future__ import annotations

import logging
from typing import Any

from ...config_file import load_cached as _load_cfg
from .chat_turn_runner import ChatTurnRunner, get_running_turn
from ..events import SessionEvent

log = logging.getLogger(__name__)


def _report_prompt(event: dict[str, Any]) -> str:
    status = event.get("status") or "unknown"
    agent = event.get("agent") or "?"
    exit_code = event.get("exit_code")
    job_id = event.get("job_id") or ""
    log_ref = event.get("log_ref") or ""
    error = event.get("error") or ""
    lines = [
        "[system] Background delegate job finished.",
        f"- agent: {agent} | status: {status} | exit_code: {exit_code}",
        f"- job_id: {job_id}",
        f"- log: {log_ref}",
    ]
    if error:
        lines.append(f"- error: {error}")
    lines.append(
        'Call delegate_status {"job_id": "' + job_id + '"} to fetch the result, '
        "then give the user a concise update (2-4 sentences) — what the "
        "delegate did, and any follow-up the outcome requires. If you already "
        "reported this exact result earlier in this conversation, reply in one "
        "line saying it was already reported."
    )
    return "\n".join(lines)


def _status_label(event: dict[str, Any]) -> str:
    status = event.get("status") or "unknown"
    code = event.get("exit_code")
    return f"{status} (exit {code})" if code is not None else str(status)


def wire_delegate_follow_up(
    *,
    sessions: Any,
    agent: Any,
    tracker: Any,
    publish_job_event: Any,
) -> Any:
    """Build the on-complete callback and return it."""

    async def on_complete(event: dict[str, Any]) -> None:
        try:
            if _load_cfg().delegate.on_complete != "report":
                return
        except Exception:  # noqa: BLE001 — config hiccup shouldn't kill the hook
            log.exception("[delegate] on_complete config check failed")
            return

        sid = event.get("session_id")
        if not sid:
            return

        # 1) Completion event on the session bus (UI banner material).
        try:
            sessions.publish(sid, SessionEvent(kind="delegate_done", data=event))
        except Exception:  # noqa: BLE001
            log.exception("[delegate] failed to publish delegate_done")

        # 2) Follow-up report turn.
        prompt = _report_prompt(event)
        runner = get_running_turn(sid)
        if runner is not None:
            # Race-free: lands before the runner's final drain check →
            # injected/chained; after it → returns None and we start a
            # fresh turn below.
            qid = runner.enqueue(prompt, origin="web")
            if qid is not None:
                return

        session = sessions.get(sid)
        if session is None:
            return
        pre_turn_history = list(session.history)
        turn_job_id = tracker.start(
            type="chat_turn",
            label=f"[delegate {event.get('agent')}] {_status_label(event)}",
            session_id=sid,
            publish_fn=publish_job_event,
        )
        try:
            from ...agent.llm import ChatMessage as _CM, Role as _R

            sessions.replace_history(
                sid, pre_turn_history + [_CM(role=_R.USER, content=prompt)]
            )
        except Exception:  # noqa: BLE001 — best-effort eager persist
            log.exception("[delegate] report-turn user message persist failed")
        try:
            sessions.clear_replay(sid)
        except Exception:  # noqa: BLE001
            pass

        report = ChatTurnRunner(
            agent=agent,
            store=sessions,
            session_id=sid,
            message=prompt,
            context=session.context or "",
            model_id="",
            pre_turn_history=pre_turn_history,
            attachment_parts=None,
            resume_working_messages=None,
            tracker=tracker,
            turn_job_id=turn_job_id,
            publish_job_event=publish_job_event,
            origin="web",
        )
        report.start()

    return on_complete
