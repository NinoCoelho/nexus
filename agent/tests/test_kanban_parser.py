"""Parser/serializer tests for the Obsidian-Kanban-compatible markdown format.

These tests verify that rich markdown inside a card body — sub-headings,
sub-lists, fenced code blocks, blockquotes — round-trips losslessly, and that
legacy ``### Title`` boards still parse and migrate on the next write.
"""

from __future__ import annotations

from nexus import vault_kanban
from nexus.vault_kanban import parser as kp_parser
from nexus.vault_kanban.models import Board, Card, Lane


def _board_with_one_card(body: str = "") -> Board:
    return Board(
        title="Test",
        lanes=[Lane(id="todo", title="Todo", cards=[Card(id="c1", title="A card", body=body)])],
    )


def test_roundtrip_preserves_subheadings_and_sublists_and_code_fences():
    body = (
        "## Sub-heading inside body\n"
        "\n"
        "Details:\n"
        "- Milk\n"
        "- Eggs\n"
        "\n"
        "```python\n"
        "print('hello')\n"
        "```\n"
        "\n"
        "> A blockquote line\n"
        "> spanning two\n"
        "\n"
        "### Another heading at H3"
    )
    board = _board_with_one_card(body)
    md = vault_kanban.serialize(board)
    reparsed = vault_kanban.parse(md)
    assert reparsed.lanes[0].cards[0].body == body


def test_card_with_dash_at_column_zero_in_body_does_not_eat_next_card():
    """Regression: body starting with `- item` at column 0 used to be
    interpreted as a sibling card, swallowing whatever followed."""
    body = "- item one\n- item two\n- item three"
    board = Board(
        title="T",
        lanes=[
            Lane(
                id="todo",
                title="Todo",
                cards=[
                    Card(id="first", title="First", body=body),
                    Card(id="second", title="Second"),
                ],
            )
        ],
    )
    md = vault_kanban.serialize(board)
    reparsed = vault_kanban.parse(md)
    assert len(reparsed.lanes[0].cards) == 2
    assert reparsed.lanes[0].cards[0].title == "First"
    assert reparsed.lanes[0].cards[0].body == body
    assert reparsed.lanes[0].cards[1].title == "Second"


def test_legacy_header_style_is_parsed_and_migrates_on_serialize():
    legacy = (
        "---\n"
        "kanban-plugin: basic\n"
        "---\n"
        "\n"
        "# Old Board\n"
        "\n"
        "## Todo\n"
        "\n"
        "### Card A\n"
        "<!-- nx:id=aaa -->\n"
        "\n"
        "Some body\n"
        "\n"
        "### Card B\n"
        "<!-- nx:id=bbb -->\n"
    )
    board = vault_kanban.parse(legacy)
    assert [c.title for c in board.lanes[0].cards] == ["Card A", "Card B"]
    assert board.lanes[0].cards[0].id == "aaa"
    assert board.lanes[0].cards[0].body == "Some body"

    migrated = vault_kanban.serialize(board)
    # Serializer always emits list-style now.
    assert "- [ ] Card A" in migrated
    assert "- [ ] Card B" in migrated
    assert "### Card A" not in migrated

    # Re-parsing the migrated form yields the same board.
    again = vault_kanban.parse(migrated)
    assert [c.title for c in again.lanes[0].cards] == ["Card A", "Card B"]
    assert again.lanes[0].cards[0].id == "aaa"
    assert again.lanes[0].cards[0].body == "Some body"


def test_checked_state_round_trips():
    board = Board(
        title="T",
        lanes=[
            Lane(
                id="todo",
                title="Todo",
                cards=[
                    Card(id="c1", title="Done thing", checked=True),
                    Card(id="c2", title="Open thing", checked=False),
                ],
            )
        ],
    )
    md = vault_kanban.serialize(board)
    assert "- [x] Done thing" in md
    assert "- [ ] Open thing" in md

    reparsed = vault_kanban.parse(md)
    assert reparsed.lanes[0].cards[0].checked is True
    assert reparsed.lanes[0].cards[1].checked is False


def test_status_done_implies_checkbox_x():
    board = _board_with_one_card()
    board.lanes[0].cards[0].status = "done"
    md = vault_kanban.serialize(board)
    assert "- [x] A card" in md


def test_tab_indented_body_is_dedented_on_parse():
    md = (
        "---\n"
        "kanban-plugin: basic\n"
        "---\n"
        "\n"
        "# T\n"
        "\n"
        "## Todo\n"
        "\n"
        "- [ ] Tabbed\n"
        "\t<!-- nx:id=t1 -->\n"
        "\tBody line 1\n"
        "\tBody line 2\n"
    )
    board = vault_kanban.parse(md)
    card = board.lanes[0].cards[0]
    assert card.id == "t1"
    assert card.body == "Body line 1\nBody line 2"


def test_indent_helpers_match_obsidian_kanban_behaviour():
    """Sanity-check the indent/dedent helpers used by the serializer/parser
    against the same regex behaviour the Obsidian Kanban plugin uses
    (``\\n(?: {4}|\\t)`` for dedent; ``\\n    `` for indent)."""
    assert kp_parser._indent_body("a\n\nb") == ["    a", "", "    b"]
    assert kp_parser._indent_body("") == [""]


def test_empty_body_card_round_trips():
    board = _board_with_one_card("")
    md = vault_kanban.serialize(board)
    reparsed = vault_kanban.parse(md)
    assert reparsed.lanes[0].cards[0].body == ""


def test_body_with_html_comment_that_is_not_nx_is_preserved():
    body = "<!-- a regular html comment -->\nfollowed by text"
    board = _board_with_one_card(body)
    md = vault_kanban.serialize(board)
    reparsed = vault_kanban.parse(md)
    assert reparsed.lanes[0].cards[0].body == body
