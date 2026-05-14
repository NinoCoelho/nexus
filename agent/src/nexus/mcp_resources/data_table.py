"""Generate self-contained HTML for a standalone data table view."""

from __future__ import annotations

import html as html_mod
import json
import logging

log = logging.getLogger(__name__)


def render_data_table(params: dict) -> str:
    from . import _wrap

    folder = params.get("folder", "")
    table_path = params.get("path", "")

    if not table_path:
        return _wrap("<p style='color:#ef4444'>Missing path parameter</p>")

    try:
        from ..vault_datatable import read_table
        dt = read_table(table_path)
    except FileNotFoundError:
        return _wrap(f"<p style='color:#ef4444'>Table not found: {html_mod.escape(table_path)}</p>")
    except Exception as e:
        log.exception("data_table render failed for %r", table_path)
        return _wrap(f"<p style='color:#ef4444'>Error: {html_mod.escape(str(e))}</p>")

    schema = dt.get("schema", {})
    rows = dt.get("rows", [])
    fields = schema.get("fields", [])
    col_names = [f.get("name", "") for f in fields if isinstance(f, dict)]
    if not col_names and rows:
        col_names = list(rows[0].keys())
    table_title = html_mod.escape(schema.get("title", table_path))

    max_rows = 50
    display_rows = rows[:max_rows]
    truncated = len(rows) > max_rows

    thead = "<tr>" + "".join(f'<th>{html_mod.escape(c)}</th>' for c in col_names) + "</tr>"
    tbody = ""
    for row in display_rows:
        tbody += "<tr>" + "".join(
            f'<td>{html_mod.escape(str(row.get(c, "")))}</td>' for c in col_names
        ) + "</tr>"

    meta = f'{len(rows)} rows'
    if truncated:
        meta += f' (showing first {max_rows})'

    body = (
        "<style>"
        ".nx-table-wrap{background:#0f172a;border-radius:8px;padding:12px}"
        ".nx-table-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}"
        ".nx-table-title{font-size:13px;font-weight:600}"
        ".nx-table-meta{color:#64748b;font-size:11px}"
        ".nx-table{width:100%;border-collapse:collapse;font-size:12px}"
        ".nx-table th{text-align:left;padding:6px 8px;border-bottom:1px solid #1e293b;color:#94a3b8;font-weight:600;white-space:nowrap}"
        ".nx-table td{padding:5px 8px;border-bottom:1px solid #1e293b33;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".nx-table tr:hover td{background:#1e293b66}"
        "</style>"
        f'<div class="nx-table-wrap">'
        f'<div class="nx-table-header"><span class="nx-table-title">{table_title}</span><span class="nx-table-meta">{meta}</span></div>'
        f'<div style="overflow-x:auto"><table class="nx-table"><thead>{thead}</thead><tbody>{tbody}</tbody></table></div>'
        f'</div>'
    )
    return _wrap(body)
