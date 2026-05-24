"""Per-board kanban card processing queue.

Serialises background dispatches so that only one card per board runs at a
time.  Cards that arrive while another is processing are queued and dispatched
in order once the running card finishes (or is cancelled).

The queue is a process-level singleton initialised by ``create_app()`` via
:func:`init_queue`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class _PendingItem:
    card_id: str
    run_fn: Any


@dataclass
class _BoardSlot:
    running_card_id: str | None = None
    running_task: asyncio.Task[Any] | None = None
    queue: deque[_PendingItem] = field(default_factory=deque)


class KanbanQueue:
    """Process-level singleton that serialises card dispatches per board."""

    def __init__(self) -> None:
        self._boards: dict[str, _BoardSlot] = {}

    def _slot(self, board_path: str) -> _BoardSlot:
        if board_path not in self._boards:
            self._boards[board_path] = _BoardSlot()
        return self._boards[board_path]

    def submit(self, board_path: str, card_id: str, run_fn: Any) -> None:
        """Submit a card for background processing.

        ``run_fn`` is a zero-arg async callable that runs the full background
        agent turn (``_run_background_agent_turn``).  If no card is currently
        running on this board, the turn starts immediately; otherwise it is
        queued and runs when the board becomes free.
        """
        slot = self._slot(board_path)
        if slot.running_task is None or slot.running_task.done():
            self._start(slot, board_path, card_id, run_fn)
        else:
            slot.queue.append(_PendingItem(card_id=card_id, run_fn=run_fn))
            log.info(
                "kanban_queue: card %s queued on %s behind %s (depth=%d)",
                card_id, board_path, slot.running_card_id, len(slot.queue),
            )

    def _start(
        self,
        slot: _BoardSlot,
        board_path: str,
        card_id: str,
        run_fn: Any,
    ) -> None:
        slot.running_card_id = card_id

        async def _wrapped() -> None:
            try:
                await run_fn()
            except asyncio.CancelledError:
                from .. import vault_kanban
                try:
                    vault_kanban.update_card(board_path, card_id, {"status": "failed"})
                except Exception:
                    log.exception("kanban_queue: cancel status update failed for %s", card_id)
                raise
            finally:
                slot.running_card_id = None
                slot.running_task = None
                self._advance(board_path)

        slot.running_task = asyncio.create_task(_wrapped())

    def _advance(self, board_path: str) -> None:
        slot = self._boards.get(board_path)
        if not slot or not slot.queue:
            return
        item = slot.queue.popleft()
        self._start(slot, board_path, item.card_id, item.run_fn)
        log.info(
            "kanban_queue: card %s dequeued on %s (remaining=%d)",
            item.card_id, board_path, len(slot.queue),
        )

    def cancel(self, board_path: str, card_id: str) -> bool:
        """Cancel a running or queued card.

        Sets the card status to ``"failed"`` and advances the queue.
        Returns ``True`` if the card was found and cancelled.
        """
        slot = self._boards.get(board_path)
        if not slot:
            return False

        if slot.running_card_id == card_id:
            if slot.running_task and not slot.running_task.done():
                slot.running_task.cancel()
            return True

        for i, item in enumerate(slot.queue):
            if item.card_id == card_id:
                slot.queue.remove(item)
                from .. import vault_kanban
                try:
                    vault_kanban.update_card(board_path, card_id, {"status": "failed"})
                except Exception:
                    log.exception("kanban_queue: queued cancel status update failed for %s", card_id)
                return True

        return False

    def is_running(self, board_path: str, card_id: str) -> bool:
        slot = self._boards.get(board_path)
        return slot is not None and slot.running_card_id == card_id

    def queue_depth(self, board_path: str) -> int:
        slot = self._boards.get(board_path)
        return len(slot.queue) if slot else 0


_queue: KanbanQueue | None = None


def get_queue() -> KanbanQueue:
    global _queue
    if _queue is None:
        _queue = KanbanQueue()
    return _queue


def init_queue() -> KanbanQueue:
    global _queue
    _queue = KanbanQueue()
    return _queue
