"""The ``ask_user`` tool — thin adapter over :class:`loom.hitl.HitlBroker`.

The heavy lifting (park-on-Future, publish/resolve, YOLO, timeout
sentinel) lives in Loom so every Loom adopter shares one HITL shape.
This module keeps Nexus's public surface:

* :data:`ASK_USER_TOOL` — the ``ToolSpec`` the agent sees.
* :class:`AskUserHandler` — validates args, delegates to the broker
  owned by ``session_store``, and maps the raw answer into an
  :class:`AskUserResult` so :class:`TerminalHandler` can keep
  destructuring ``ok`` / ``answer`` / ``kind`` / ``timed_out`` / ``error``.

Three interaction kinds are supported (``confirm``, ``choice``,
``text``). Kinds and the timeout sentinel come from
``loom.hitl`` so Loom's side can never drift from Nexus's.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loom.hitl import TIMEOUT_SENTINEL

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
_VALID_KINDS = {"confirm", "choice", "text"}


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

    * ``session_store`` — owns the underlying ``HitlBroker`` via its
      ``.broker`` property. All publish / register / resolve / cancel
      work flows through that broker.
    * ``yolo_mode_getter`` — a callable returning the current YOLO
      setting. Plumbed here (not a snapshot) so Settings hot-toggle
      takes effect on the next ``ask_user`` without reconstruction.
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
        if kind not in _VALID_KINDS:
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

        # Delegate to loom.hitl.HitlBroker — it handles YOLO short-circuit
        # (publishing ``user_request_auto``), park-on-Future, timeout +
        # ``user_request_cancelled`` emission, and publish-hook fan-out
        # to our SessionEvent SSE stream.
        answer = await self._sessions.broker.ask(
            session_id,
            prompt,
            kind=kind,
            choices=choices if kind == "choice" else None,
            default=default,
            timeout_seconds=int(timeout),
            yolo=(kind == "confirm" and self._yolo()),
        )
        timed_out = answer == TIMEOUT_SENTINEL
        return AskUserResult(
            ok=True,
            answer=answer,
            kind=kind,
            timed_out=timed_out,
        )


def _error(*, kind: str, message: str) -> AskUserResult:
    return AskUserResult(
        ok=False, answer=None, kind=kind, timed_out=False, error=message
    )
