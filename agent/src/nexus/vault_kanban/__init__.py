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

    - [ ] Card title
        <!-- nx:id=<uuid> -->
        <!-- nx:session=<sid> -->   (optional, linked chat session)

        Optional card body / notes — rich markdown is preserved verbatim
        because the whole block is indented as a list-item continuation:

        ## Sub-heading inside body works
        - sub-list item
        ```python
        fenced code blocks too
        ```

    - [ ] Another card
        <!-- nx:id=<uuid> -->

    ## Another lane

Cards are emitted as GFM task-list items (``- [ ] title`` or ``- [x] title``)
matching the Obsidian Kanban plugin's on-disk format. All metadata and body
content live inside the list item, indented with 4 spaces or a tab — the same
rule CommonMark uses for list-item continuation. This means *anything* inside
the body (sub-headings, sub-lists, code fences, blockquotes) round-trips
losslessly without confusing the parser.

For backwards compatibility the parser still accepts the older header-style
(``### Card title`` followed by free body text); the serializer always emits
the list-style. Older boards migrate automatically on the next write.

The plain-markdown file remains editable by hand and renders sensibly in any
markdown viewer — the ``<!-- nx:* -->`` comments are invisible.

Re-exports all public symbols so ``from nexus import vault_kanban`` and
attribute access like ``vault_kanban.parse(...)`` keep working.
"""

from .boards import create_empty, list_boards, query_boards, read_board, update_board, write_board
from .cards import add_card, delete_card, move_card, update_card
from .hooks import LaneChangeHook, _lane_change_hook, set_lane_change_hook
from .lanes import add_lane, delete_lane, move_lane, update_lane
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
    "update_board",
    "create_empty",
    "list_boards",
    "query_boards",
    # cards
    "add_card",
    "update_card",
    "move_card",
    "delete_card",
    # lanes
    "add_lane",
    "update_lane",
    "move_lane",
    "delete_lane",
    # hooks
    "LaneChangeHook",
    "set_lane_change_hook",
    "_lane_change_hook",
]
