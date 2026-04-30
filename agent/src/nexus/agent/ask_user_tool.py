"""The ``ask_user`` tool — thin adapter over :class:`loom.hitl.HitlBroker`.

The heavy lifting (park-on-Future, publish/resolve, YOLO, timeout
sentinel) lives in Loom so every Loom adopter shares one HITL shape.
This module keeps Nexus's public surface:

* :data:`ASK_USER_TOOL` — the ``ToolSpec`` the agent sees.
* :class:`AskUserHandler` — validates args, delegates to the broker
  owned by ``session_store``, and maps the raw answer into an
  :class:`AskUserResult` so :class:`TerminalHandler` can keep
  destructuring ``ok`` / ``answer`` / ``kind`` / ``timed_out`` / ``error``.

Four interaction kinds are supported (``confirm``, ``choice``, ``text``,
``form``). Kinds and the timeout sentinel come from ``loom.hitl`` so
Loom's side can never drift from Nexus's.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loom.hitl import TIMEOUT_SENTINEL

from .ask_user_schema import ASK_USER_TOOL  # noqa: F401 — re-exported
from .context import CURRENT_SESSION_ID
from .form_schema import FieldSchema, FormSchema
from .llm import ToolSpec  # noqa: F401 — imported by callers via this module

_DEFAULT_TIMEOUT_SECONDS = 300
_VALID_KINDS = {"confirm", "choice", "text", "form"}

# How long ask_user waits synchronously before parking a parkable request.
# The number is small on purpose: parkable kinds are async by design, so
# every additional second we hold the turn open is a second we burn the
# prompt cache and pay LLM throughput for the privilege of doing nothing.
_PARK_THRESHOLD_SECONDS = 30

# Sentinel returned from a parkable ask_user when the user did not answer
# within the threshold. The agent façade detects this in tool_exec_result,
# persists the working snapshot, emits a `parked` event, and ends the turn.
# Non-parked timeouts still use loom.hitl.TIMEOUT_SENTINEL.
_PARKED_SENTINEL_PREFIX = "__parked__:"


def parked_sentinel(request_id: str) -> str:
    return f"{_PARKED_SENTINEL_PREFIX}{request_id}"


def parse_parked_sentinel(text: str | None) -> str | None:
    if not isinstance(text, str) or not text.startswith(_PARKED_SENTINEL_PREFIX):
        return None
    return text[len(_PARKED_SENTINEL_PREFIX):] or None


@dataclass(frozen=True)
class AskUserResult:
    ok: bool
    answer: str | dict | None
    kind: str
    timed_out: bool
    error: str | None = None
    # Names of form fields that were declared ``secret: true``. The raw
    # value stays in ``answer`` so server-side callers (e.g. the skill
    # credential prompt) can read it; ``to_text`` — which produces the
    # JSON the LLM sees — replaces those values with ``"[redacted]"``.
    secret_fields: tuple[str, ...] = ()

    def to_text(self) -> str:
        answer_for_llm: str | dict | None = self.answer
        if self.secret_fields and isinstance(answer_for_llm, dict):
            answer_for_llm = {
                k: ("[redacted]" if k in self.secret_fields else v)
                for k, v in answer_for_llm.items()
            }
        return json.dumps(
            {
                "ok": self.ok,
                "answer": answer_for_llm,
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
        # Side-dict for form requests: maps request_id → extra payload data
        # so /pending can reconstruct the full form schema on page reload.
        self._form_extras: dict[str, dict[str, Any]] = {}

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
        fields: list[dict] | None = None
        if kind == "form":
            raw_fields = args.get("fields")
            if not isinstance(raw_fields, list) or not raw_fields:
                return _error(
                    kind=kind,
                    message="kind='form' requires a non-empty `fields` array",
                )
            try:
                validated = FormSchema(
                    title=args.get("title"),
                    description=args.get("description"),
                    fields=[FieldSchema.model_validate(f) for f in raw_fields],
                )
                fields = [f.model_dump(exclude_none=True) for f in validated.fields]
            except Exception as exc:
                return _error(kind=kind, message=f"invalid fields: {exc}")
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

        # Parking eligibility:
        #  - kind='form' always parks (forms are async by design — fields can
        #    take real time to fill out, often after a context switch).
        #  - kind='text' / 'choice' park only when the caller opts in via
        #    `parkable: true`. Default is synchronous so existing skills
        #    that ask quick questions don't suddenly drop the turn.
        #  - kind='confirm' never parks. Approvals are semantically "now-or-
        #    never" — a stale yes/no on a destructive action read days later
        #    is dangerous; we'd rather time out and force the agent to
        #    re-ask in a fresh context.
        parkable_opt_in = bool(args.get("parkable", False))
        is_parkable = kind == "form" or (
            kind in ("text", "choice") and parkable_opt_in
        )

        secret_field_names: tuple[str, ...] = ()
        if kind == "form" and fields:
            secret_field_names = tuple(
                f.get("name", "") for f in fields if f.get("secret")
            )

        if is_parkable:
            return await self._ask_parkable(
                session_id=session_id,
                prompt=prompt,
                kind=kind,
                choices=choices if kind == "choice" else None,
                fields=fields or [] if kind == "form" else None,
                form_title=args.get("title") if kind == "form" else None,
                form_description=args.get("description") if kind == "form" else None,
                default=default,
                timeout_seconds=int(timeout),
                secret_field_names=secret_field_names,
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

    async def _ask_parkable(
        self,
        *,
        session_id: str,
        prompt: str,
        kind: str,
        choices: list[str] | None,
        fields: list[dict] | None,
        form_title: str | None,
        form_description: str | None,
        default: str | None,
        timeout_seconds: int,
        secret_field_names: tuple[str, ...] = (),
    ) -> AskUserResult:
        """Park-after-threshold HITL.

        Waits up to ``_PARK_THRESHOLD_SECONDS`` for an answer. If none
        arrives, persists a ``hitl_pending`` row and returns the parked
        sentinel — the agent façade detects it and ends the turn cleanly.
        ``parked_messages_json`` is left empty here; the façade backfills
        it once it knows the working snapshot at dispatch time.
        """
        from ..server.events import SessionEvent

        # Supersede any parked form on this session before opening a new one.
        # If the agent re-asks the same form (e.g., a retry loop after a bad
        # answer), the user would otherwise see the old form re-published on
        # every reconnect. Cancel and broadcast so the UI removes the stale
        # entry from queue + bell.
        if kind == "form":
            try:
                stale = self._sessions.list_pending_for_session(
                    session_id, kind="form",
                )
            except Exception:  # noqa: BLE001 — best-effort
                stale = []
            for row in stale:
                stale_rid = row.get("request_id")
                if not stale_rid:
                    continue
                try:
                    self._sessions.cancel_hitl_pending(
                        stale_rid, reason="superseded",
                    )
                    self._sessions.publish(
                        session_id,
                        SessionEvent(
                            kind="user_request_cancelled",
                            data={
                                "request_id": stale_rid,
                                "reason": "superseded",
                            },
                        ),
                    )
                except Exception:  # noqa: BLE001 — best-effort
                    pass
                self._form_extras.pop(stale_rid, None)

        request_id = uuid.uuid4().hex
        if kind == "form":
            extra_data: dict[str, Any] = {
                "fields": fields or [],
                "form_title": form_title,
                "form_description": form_description,
            }
            self._form_extras[request_id] = extra_data

        fut: asyncio.Future[str] = self._sessions.register_pending(
            session_id, request_id
        )
        event_data: dict[str, Any] = {
            "request_id": request_id,
            "prompt": prompt,
            "kind": kind,
            "choices": choices,
            "default": default,
            "timeout_seconds": timeout_seconds,
        }
        if kind == "form":
            event_data["fields"] = fields or []
            event_data["form_title"] = form_title
            event_data["form_description"] = form_description
        self._sessions.publish(
            session_id, SessionEvent(kind="user_request", data=event_data),
        )

        wait_for = min(_PARK_THRESHOLD_SECONDS, max(1, int(timeout_seconds)))
        try:
            raw = await asyncio.wait_for(asyncio.shield(fut), timeout=wait_for)
        except asyncio.TimeoutError:
            # Park: persist enough state to resume after restart, cancel the
            # broker future so a future /respond goes through the resume
            # endpoint instead of a stale in-memory promise, and return the
            # sentinel for the agent façade to act on.
            self._sessions.cancel_pending(session_id, request_id)
            tool_call_id = (
                self._sessions.get_pending_tool_call(session_id) or ""
            )
            try:
                self._sessions.persist_hitl_pending(
                    session_id=session_id,
                    request_id=request_id,
                    tool_call_id=tool_call_id,
                    kind=kind,
                    prompt=prompt,
                    choices=choices,
                    fields=fields if kind == "form" else None,
                    form_title=form_title,
                    form_description=form_description,
                    default=default,
                    timeout_seconds=int(timeout_seconds),
                )
            except Exception:  # noqa: BLE001 — best-effort
                # Even if persistence fails the parked sentinel still ends
                # the turn cleanly; we just won't be able to resume.
                pass
            return AskUserResult(
                ok=True,
                answer=parked_sentinel(request_id),
                kind=kind,
                timed_out=False,
                secret_fields=secret_field_names,
            )
        except asyncio.CancelledError:
            self._form_extras.pop(request_id, None)
            return AskUserResult(
                ok=True,
                answer=TIMEOUT_SENTINEL,
                kind=kind,
                timed_out=True,
                secret_fields=secret_field_names,
            )

        # Answered within the threshold — clean up and decode.
        self._form_extras.pop(request_id, None)
        decoded: str | dict = raw
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
        return AskUserResult(
            ok=True,
            answer=decoded,
            kind=kind,
            timed_out=False,
            secret_fields=secret_field_names,
        )


def _error(*, kind: str, message: str) -> AskUserResult:
    return AskUserResult(
        ok=False, answer=None, kind=kind, timed_out=False, error=message
    )
