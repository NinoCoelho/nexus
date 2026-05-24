"""Internal MCP resource generators for Nexus views.

Generates self-contained HTML pages for the ``ui://nexus/*`` URI scheme,
consumed by the ``McpAppSandbox`` iframe in the chat UI.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)


def resolve(uri: str) -> str:
    """Dispatch a ``ui://nexus/<view>`` URI and return HTML.

    Query parameters are forwarded to the generator as keyword arguments.
    """
    parsed = urlparse(uri)
    view = parsed.path.lstrip("/")
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    if view == "kanban":
        from .kanban import render_kanban
        return render_kanban(params.get("path", ""))
    if view == "dashboard-widget":
        from .dashboard import render_widget
        return render_widget(params.get("folder", ""), params.get("widget_id", ""))
    if view == "data-table":
        from .data_table import render_data_table
        return render_data_table(params)
    return _error_html(f"Unknown view: {view}")


def _error_html(message: str) -> str:
    return _wrap(f"<p style='color:#ef4444'>{message}</p>")


def _wrap(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>"
        "*{margin:0;padding:0;box-sizing:border-box}"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "background:#1a1a2e;color:#e0e0e0;font-size:13px;padding:12px}"
        "a{color:#60a5fa;text-decoration:none}"
        "a:hover{text-decoration:underline}"
        "</style></head><body>"
        + body
        + "</body></html>"
    )
