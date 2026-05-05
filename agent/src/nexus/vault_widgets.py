"""Per-widget result storage as vault files.

Each widget configured on a database dashboard (`_data.md` → `widgets`) has a
companion result file at::

    <folder>/_widgets/<widget_id>.json

The file holds the latest query execution result — a JSON object with
``{columns, rows, row_count}``. The file is overwritten on every execute;
we don't keep history (history is what the chat is for).

Storing the body in a vault file means the user can browse, copy, or open
the artifact like any other vault note. Deleting the parent database via
``vault_dashboard.delete_database`` cleans up the ``_widgets/`` subtree as a
side effect of recursive folder removal.
"""

from __future__ import annotations

import re

from . import vault

WIDGETS_SUBDIR = "_widgets"

_SLUG_RE = re.compile(r"^[a-z0-9_][a-z0-9_\-]*$")


def widget_path(folder: str, widget_id: str) -> str:
    """Vault-relative path to a widget's result file."""
    if not _SLUG_RE.match(widget_id):
        raise ValueError(f"widget_id {widget_id!r} must be a slug")
    folder = (folder or "").strip("/")
    base = f"{WIDGETS_SUBDIR}/{widget_id}.json"
    return f"{folder}/{base}" if folder else base


def read_widget_result(folder: str, widget_id: str) -> str:
    """Return the widget's current result body, or ``""`` if not yet executed."""
    try:
        file = vault.read_file(widget_path(folder, widget_id))
    except (FileNotFoundError, OSError):
        return ""
    return file.get("content", "") or ""


def write_widget_result(folder: str, widget_id: str, body: str) -> None:
    """Overwrite the widget's result file with ``body`` verbatim.

    Caller is responsible for keeping ``body`` as valid JSON.
    """
    vault.write_file(widget_path(folder, widget_id), body or "")


def delete_widget_result(folder: str, widget_id: str) -> None:
    """Delete the widget's result file. No-op if it doesn't exist."""
    path = widget_path(folder, widget_id)
    try:
        vault.delete(path)
    except (FileNotFoundError, OSError):
        pass
