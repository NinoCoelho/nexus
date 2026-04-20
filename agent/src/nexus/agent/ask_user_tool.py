"""The ``ask_user`` tool — the one HITL primitive every approval flow
composes on top of.

Emits a ``user_request`` SSE event carrying the prompt + options, then
parks on a Future until the UI POSTs the answer via ``/respond``. On
timeout the Future is cancelled and the tool returns a sentinel so the
agent can gracefully decline the action rather than hang.

Three interaction kinds are supported:

* ``confirm`` — yes/no. UI renders two buttons.
* ``choice`` — pick one of several. UI renders option buttons.
* ``text`` — free-form input. UI renders a text field.

Skills use this by calling the tool directly. The tool is what makes
HITL uniform across the system: ``terminal`` composes on top (approval
before shell exec), and any skill author can compose on top by writing
"call ``ask_user`` before doing X" in their markdown.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context import CURRENT_SESSION_ID
from .llm import ToolSpec

ASK_USER_TOOL = ToolSpec(
    name="ask_user",
    description=(
        "Pause the agent and ask the user a question. Use when the next "
        "step requires their judgment — confirming a destructive action, "
        "picking between options only they know, or asking for a value "
        "(URL, filename, number). Returns the user's answer as a string. "
        "If the user doesn't respond within the timeout (default 300s), "
        "returns the literal string '__timeout__' — treat that as 'do "
        "not proceed'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The question to show the user. Be specific: include "
                    "the exact action, the target, and any side effects."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["confirm", "choice", "text"],
                "description": (
                    "'confirm' for yes/no; 'choice' for a pick-one from "
                    "`choices`; 'text' for free-form input. Default: 'confirm'."
                ),
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Required for kind='choice'. Each string is a "
                    "selectable option; the returned answer is one of "
                    "these verbatim."
                ),
            },
            "default": {
                "type": "string",
                "description": (
                    "Optional suggested answer (shown in the UI as the "
                    "preferred option / default text). Does not "
                    "auto-apply — the user still has to pick."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "How long to wait for an answer. Default 300. "
                    "Shorter for low-stakes confirms; longer for "
                    "questions that need investigation."
                ),
            },
        },
        "required": ["prompt"],
    },
)

_DEFAULT_TIMEOUT_SECONDS = 300
_TIMEOUT_SENTINEL = "__timeout__"


@dataclass(frozen=True)
class AskUserResult:
    ok: bool
    answer: str | None
    kind: str
    timed_out: bool
    error: str | None = None

    def to_text(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "answer": self.answer,
                "kind": self.kind,
                "timed_out": self.timed_out,
                "error": self.error,
            },
            ensure_ascii=False,
        )


class AskUserHandler:
    """Invoked by the agent loop when the model calls ``ask_user``.

    Depends on two collaborators injected by the server at wire time:

    * ``session_store`` — owns pending-futures and SSE publish.
    * ``yolo_mode_getter`` — a callable returning the current YOLO
      setting. Plumbed here (not a snapshot) so Settings hot-toggle
      takes effect on the next ``ask_user`` without reconstruction.

    Tests inject fakes / None and drive timeouts explicitly.
    """

    def __init__(
        self,
        *,
        session_store: Any = None,  # typed as Any to avoid circular import
        yolo_mode_getter: Callable[[], bool] | None = None,
        default_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._sessions = session_store
        self._yolo = yolo_mode_getter or (lambda: False)
        self._default_timeout = default_timeout

    async def invoke(self, args: dict[str, Any]) -> AskUserResult:
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return _error(
                kind="confirm",
                message="`prompt` is required and must be a non-empty string",
            )
        kind = args.get("kind", "confirm")
        if kind not in {"confirm", "choice", "text"}:
            return _error(kind=kind, message=f"unsupported kind {kind!r}")
        choices = args.get("choices")
        if kind == "choice":
            if not isinstance(choices, list) or not choices:
                return _error(
                    kind=kind,
                    message="kind='choice' requires a non-empty `choices` array",
                )
            if not all(isinstance(c, str) and c for c in choices):
                return _error(
                    kind=kind,
                    message="`choices` entries must be non-empty strings",
                )
        default = args.get("default")
        if default is not None and not isinstance(default, str):
            return _error(kind=kind, message="`default` must be a string if provided")
        timeout = args.get("timeout_seconds", self._default_timeout)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return _error(
                kind=kind, message="`timeout_seconds` must be a positive number"
            )

        if self._sessions is None:
            return _error(
                kind=kind,
                message=(
                    "ask_user is unavailable: session_store not wired. "
                    "This tool only works when called through a live /chat session."
                ),
            )

        session_id = CURRENT_SESSION_ID.get()
        if session_id is None:
            return _error(
                kind=kind,
                message=(
                    "ask_user is unavailable outside a /chat turn. "
                    "Session context not set."
                ),
            )

        # YOLO escape hatch: auto-confirm without prompting the UI, but
        # still publish an event so the transcript records the decision.
        # Only applies to kind=confirm — choice/text need real input and
        # auto-answering them would be silently guessing.
        if kind == "confirm" and self._yolo():
            self._sessions.publish(
                session_id,
                _event(
                    "user_request_auto",
                    {
                        "prompt": prompt,
                        "kind": kind,
                        "answer": "yes",
                        "reason": "YOLO mode enabled",
                    },
                ),
            )
            return AskUserResult(
                ok=True, answer="yes", kind=kind, timed_out=False
            )

        request_id = uuid.uuid4().hex
        try:
            future = self._sessions.register_pending(session_id, request_id)
        except (KeyError, ValueError) as exc:
            return _error(kind=kind, message=str(exc))

        payload = {
            "request_id": request_id,
            "prompt": prompt,
            "kind": kind,
            "choices": choices if kind == "choice" else None,
            "default": default,
            "timeout_seconds": int(timeout),
        }
        self._sessions.publish(session_id, _event("user_request", payload))

        try:
            answer = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # Clean up the registry entry so a late /respond doesn't
            # find a zombie request.
            self._sessions.cancel_pending(session_id, request_id)
            self._sessions.publish(
                session_id,
                _event(
                    "user_request_cancelled",
                    {"request_id": request_id, "reason": "timeout"},
                ),
            )
            return AskUserResult(
                ok=True,
                answer=_TIMEOUT_SENTINEL,
                kind=kind,
                timed_out=True,
            )
        except asyncio.CancelledError:
            # Session was reset / deleted mid-wait. Surface as a timeout
            # so the agent doesn't act on stale state.
            self._sessions.publish(
                session_id,
                _event(
                    "user_request_cancelled",
                    {"request_id": request_id, "reason": "session_reset"},
                ),
            )
            return AskUserResult(
                ok=True,
                answer=_TIMEOUT_SENTINEL,
                kind=kind,
                timed_out=True,
            )

        return AskUserResult(ok=True, answer=answer, kind=kind, timed_out=False)


def _event(kind: str, data: dict[str, Any]):
    """Tiny local helper — imported lazily to avoid circular server
    imports from the agent package."""
    from ..server.events import SessionEvent

    return SessionEvent(kind=kind, data=data)


def _error(*, kind: str, message: str) -> AskUserResult:
    return AskUserResult(
        ok=False, answer=None, kind=kind, timed_out=False, error=message
    )
