"""Vault-native kanban — Obsidian Kanban plugin format, single .md file.

Format
------
A kanban file has YAML frontmatter with ``kanban-plugin: basic`` and markdown
body organized as:

    ---
    kanban-plugin: basic
    ---

    # Board title

    ## Lane title
    <!-- nx:lane-id=<id> -->   (optional)

    ### Card title
    <!-- nx:id=<uuid> -->
    <!-- nx:session=<sid> -->   (optional, linked chat session)

    optional card body / notes

    ### Another card
    <!-- nx:id=<uuid> -->

    ## Another lane

Cards also parse from GFM task-list syntax (``- [ ] title``) for Obsidian
plugin compatibility, but we always serialize as ``###`` headings with
``<!-- nx:id=... -->`` so IDs survive reloads.

The plain-markdown file remains editable by hand and renders sensibly in any
markdown viewer — the ``<!-- nx:* -->`` comments are invisible.

Re-exports all public symbols so ``from nexus import vault_kanban`` and
attribute access like ``vault_kanban.parse(...)`` keep working.
"""

from .boards import create_empty, query_boards, read_board, write_board
from .cards import add_card, delete_card, move_card, update_card
from .hooks import LaneChangeHook, _lane_change_hook, set_lane_change_hook
from .lanes import add_lane, delete_lane, update_lane
from .models import (
    CARD_PRIORITIES,
    CARD_STATUSES,
    KANBAN_PLUGIN_KEY,
    Board,
    Card,
    Lane,
    is_kanban_file,
)
from .parser import parse, serialize

__all__ = [
    # models
    "Board",
    "Card",
    "Lane",
    "CARD_PRIORITIES",
    "CARD_STATUSES",
    "KANBAN_PLUGIN_KEY",
    "is_kanban_file",
    # parser
    "parse",
    "serialize",
    # boards
    "read_board",
    "write_board",
    "create_empty",
    "query_boards",
    # cards
    "add_card",
    "update_card",
    "move_card",
    "delete_card",
    # lanes
    "add_lane",
    "update_lane",
    "delete_lane",
    # hooks
    "LaneChangeHook",
    "set_lane_change_hook",
    "_lane_change_hook",
]
