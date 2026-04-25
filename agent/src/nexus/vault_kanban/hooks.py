"""Lane-change hook — module-level slot to avoid circular imports."""

from __future__ import annotations

from typing import Callable

# Hook fired after a successful cross-lane move. The server registers a
# callback that auto-dispatches the destination lane's prompt (with a
# loop/depth guard) so the agent's tool-driven moves get the same auto-run
# behavior as a UI drag-drop. Kept as a module-level slot to avoid a circular
# import between vault_kanban and the server layer.
LaneChangeHook = Callable[..., None]
_lane_change_hook: LaneChangeHook | None = None


def set_lane_change_hook(fn: LaneChangeHook | None) -> None:
    """Register a callback fired by ``move_card`` after a cross-lane move.

    The callback receives kwargs: ``path``, ``card_id``, ``src_lane_id``,
    ``dst_lane_id``, ``dst_lane_prompt`` (may be None — caller decides
    whether to act).
    """
    global _lane_change_hook
    _lane_change_hook = fn
