"""Generate self-contained HTML for a kanban board view."""

from __future__ import annotations

import html as html_mod
import json
import logging

log = logging.getLogger(__name__)


def render_kanban(path: str) -> str:
    from ..vault_kanban import read_board
    from . import _wrap

    if not path:
        return _wrap("<p style='color:#ef4444'>Missing path parameter</p>")

    try:
        board = read_board(path)
    except FileNotFoundError:
        return _wrap(f"<p style='color:#ef4444'>Board not found: {html_mod.escape(path)}</p>")
    except Exception as e:
        log.exception("kanban render failed for %r", path)
        return _wrap(f"<p style='color:#ef4444'>Error: {html_mod.escape(str(e))}</p>")

    data = board.to_dict()
    board_json = json.dumps(data)

    board_title = html_mod.escape(data.get("title", "Kanban"))
    lanes = data.get("lanes", [])
    total_cards = sum(len(ln.get("cards", [])) for ln in lanes)

    lanes_html = ""
    for lane in lanes:
        lane_title = html_mod.escape(lane.get("title", ""))
        cards = lane.get("cards", [])
        cards_html = ""
        for card in cards:
            card_title = html_mod.escape(card.get("title", ""))
            badges = ""
            if card.get("priority"):
                color = {"urgent": "#ef4444", "high": "#f97316", "med": "#eab308", "low": "#22c55e"}.get(
                    card["priority"], "#888"
                )
                badges += f'<span style="background:{color};color:#fff;font-size:10px;padding:1px 5px;border-radius:3px;margin-right:4px">{html_mod.escape(card["priority"])}</span>'
            if card.get("due"):
                badges += f'<span style="background:#334155;color:#94a3b8;font-size:10px;padding:1px 5px;border-radius:3px">{html_mod.escape(card["due"])}</span>'
            if card.get("labels"):
                for lbl in card["labels"]:
                    badges += f'<span style="background:#1e3a5f;color:#60a5fa;font-size:10px;padding:1px 5px;border-radius:3px;margin-left:4px">{html_mod.escape(lbl)}</span>'
            body_preview = ""
            if card.get("body"):
                preview = card["body"][:120].replace("\n", " ")
                body_preview = f'<div style="color:#94a3b8;font-size:11px;margin-top:4px">{html_mod.escape(preview)}</div>'
            cards_html += (
                f'<div class="nx-card">'
                f'<div style="font-weight:500;margin-bottom:3px">{card_title}</div>'
                f'{badges}'
                f'{body_preview}'
                f'</div>'
            )
        empty_msg = '<div style="color:#475569;font-size:11px;padding:8px">No cards</div>'
        lanes_html += (
            f'<div class="nx-lane">'
            f'<div class="nx-lane-header"><span>{lane_title}</span><span style="color:#64748b;font-size:11px">{len(cards)}</span></div>'
            f'<div class="nx-lane-cards">{cards_html if cards_html else empty_msg}</div>'
            f'</div>'
        )

    body = (
        "<style>"
        ".nx-board{display:flex;flex-direction:column;gap:8px}"
        ".nx-board-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}"
        ".nx-board-title{font-size:16px;font-weight:600}"
        ".nx-board-meta{color:#64748b;font-size:11px}"
        ".nx-lanes{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}"
        ".nx-lane{min-width:200px;max-width:280px;flex:1;background:#0f172a;border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:6px}"
        ".nx-lane-header{display:flex;justify-content:space-between;align-items:center;font-size:12px;font-weight:600;padding-bottom:6px;border-bottom:1px solid #1e293b}"
        ".nx-lane-cards{display:flex;flex-direction:column;gap:6px}"
        ".nx-card{background:#1e293b;border-radius:6px;padding:8px 10px;font-size:12px;line-height:1.4}"
        "</style>"
        f'<div class="nx-board">'
        f'<div class="nx-board-header"><span class="nx-board-title">{board_title}</span><span class="nx-board-meta">{len(lanes)} lanes &middot; {total_cards} cards</span></div>'
        f'<div class="nx-lanes">{lanes_html}</div>'
        f'</div>'
    )
    return _wrap(body)
