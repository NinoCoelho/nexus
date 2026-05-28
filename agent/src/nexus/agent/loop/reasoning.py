"""Reasoning content tracking for thinking-model turns.

DeepSeek-R1, Ollama GLM-4.7-flash, and other "thinking" models emit
a ``reasoning_content`` field on assistant messages.  Loom's internal
``ChatMessage`` type has no such field, so reasoning is stripped on
the round-trip.  This module captures reasoning per LLM iteration and
stamps it back onto persisted messages before the ``done`` event.
"""

from __future__ import annotations

from typing import Any

from ..llm import ChatMessage, Role


class ReasoningTracker:
    def __init__(self) -> None:
        self._per_iter: list[str | None] = []

    def capture(self, adapter: Any) -> None:
        rc = getattr(adapter, "_last_reasoning_content", None) if adapter else None
        self._per_iter.append(rc)

    def reset(self) -> None:
        self._per_iter.clear()

    @property
    def per_iter(self) -> list[str | None]:
        return self._per_iter

    def stamp_onto(
        self,
        messages: list[ChatMessage],
        history_snapshot: list[ChatMessage],
    ) -> list[ChatMessage]:
        _snap_len = len(history_snapshot)
        _snap_rc: dict[int, str] = {}
        for _si, _sm in enumerate(history_snapshot):
            if _sm.role == Role.ASSISTANT and _sm.reasoning_content:
                _snap_rc[_si] = _sm.reasoning_content
        _rc_idx = 0
        for _mi in range(len(messages)):
            msg = messages[_mi]
            if msg.role != Role.ASSISTANT:
                continue
            if _mi < _snap_len:
                if _mi in _snap_rc:
                    messages[_mi] = msg.model_copy(
                        update={"reasoning_content": _snap_rc[_mi]}
                    )
            else:
                if _rc_idx < len(self._per_iter):
                    rc_val = self._per_iter[_rc_idx]
                    _rc_idx += 1
                    if rc_val:
                        messages[_mi] = msg.model_copy(
                            update={"reasoning_content": rc_val}
                        )
        return messages

    def stamp_onto_prefix_only(
        self,
        messages: list[ChatMessage],
        prefix: list[ChatMessage],
    ) -> list[ChatMessage]:
        _snap_rc: dict[int, str] = {}
        for _si, _sm in enumerate(prefix):
            if _sm.role == Role.ASSISTANT and _sm.reasoning_content:
                _snap_rc[_si] = _sm.reasoning_content
        for _mi in range(min(len(prefix), len(messages))):
            if _mi in _snap_rc:
                messages[_mi] = messages[_mi].model_copy(
                    update={"reasoning_content": _snap_rc[_mi]}
                )
        return messages

    def hydrate_adapter_map(
        self, adapter: Any, history: list[ChatMessage]
    ) -> None:
        if adapter is None or not hasattr(adapter, "_reasoning_content_list"):
            return
        rc_list: list[str] = []
        for m in history:
            if m.role == Role.ASSISTANT and m.reasoning_content:
                rc_list.append(m.reasoning_content)
        adapter._reasoning_content_list = rc_list

    def clear_adapter_map(self, adapter: Any) -> None:
        if adapter is not None and hasattr(adapter, "_reasoning_content_list"):
            adapter._reasoning_content_list = []
