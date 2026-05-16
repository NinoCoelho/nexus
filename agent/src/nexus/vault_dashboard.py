"""Per-database dashboard config — a folder-level marker file `_data.md`.

A vault folder that's already a "database" (contains ≥1 ``data-table-plugin``
file) can have a sibling ``_data.md`` that holds dashboard config:

* Quick-action **operations** (chat or form) shown as chips on the dashboard.
* The id of a chat session bound to this database (the floating chat bubble's
  conversation thread).

The file is **lazy**: callers can read a `default_dashboard()` shape without
touching disk, and only `write_dashboard()` materializes the file. Folders
without `_data.md` keep working — the dashboard simply renders empty chips.

Format (mirrors ``data-table-plugin`` and ``kanban-plugin`` conventions)::

    ---
    data-dashboard: basic
    ---

    ## Dashboard
    ```yaml
    chat_session_id: 01HXY...        # nullable
    operations:
      - id: op_add_customer          # stable slug
        label: "Add customer"
        kind: chat                   # "chat" | "form"
        prompt: "Add a new customer named {name} with email {email}."
        icon: "user-plus"            # optional
        order: 0
      - id: op_quick_order
        label: "Quick add order"
        kind: form
        table: "./orders.md"
        prefill: { status: "open" }
        order: 1
    schema_version: 1
    ```
"""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from . import vault

DASHBOARD_PLUGIN_KEY = "data-dashboard"
DASHBOARD_FILENAME = "_data.md"
SCHEMA_VERSION = 1

_FENCE_RE = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_DASHBOARD_SECTION = re.compile(r"^##\s+Dashboard\s*$", re.MULTILINE | re.IGNORECASE)

_SLUG_RE = re.compile(r"^[a-z0-9_][a-z0-9_\-]*$")

_VIZ_TYPES = ("bar", "line", "area", "pie", "donut", "table", "kpi")
_WIDGET_REFRESH = ("manual", "daily")

# Coarse size buckets for widgets in the grid. Stored on the widget config
# so the user's choice survives reload. Absent ⇒ per-kind default applied
# at render time (chart = md, report = md, kpi = sm).
_WIDGET_SIZES = ("sm", "md", "lg")


def dashboard_path(folder: str) -> str:
    """Vault-relative path to the dashboard file for a folder."""
    folder = (folder or "").strip("/")
    return f"{folder}/{DASHBOARD_FILENAME}" if folder else DASHBOARD_FILENAME


def _folder_basename(folder: str) -> str:
    folder = (folder or "").strip("/")
    if not folder:
        return "(root)"
    return PurePosixPath(folder).name or folder


def is_dashboard_file(content: str) -> bool:
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and DASHBOARD_PLUGIN_KEY in fm


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    try:
        fm = yaml.safe_load(content[3:end]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, content[end + 4:].lstrip("\n")


def _extract_dashboard_yaml(body: str) -> dict[str, Any]:
    m_sec = _DASHBOARD_SECTION.search(body)
    if not m_sec:
        return {}
    after = body[m_sec.end():]
    m_fence = _FENCE_RE.search(after)
    if not m_fence:
        return {}
    try:
        parsed = yaml.safe_load(m_fence.group(1))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_table_path(folder: str, table: str) -> str:
    """Resolve an operation's ``table`` to a vault-absolute path.

    Bare filenames (``issues.md``) and folder-relative paths (``./orders.md``,
    ``../other/x.md``) are resolved against the dashboard's folder. Paths that
    already contain a slash and don't start with ``./`` or ``../`` are treated
    as vault-absolute and only normalized.
    """
    table = (table or "").strip()
    if not table:
        return ""
    if "/" in table and not table.startswith("./") and not table.startswith("../"):
        return posixpath.normpath(table)
    folder = (folder or "").strip("/")
    if folder:
        return posixpath.normpath(posixpath.join(folder, table))
    return posixpath.normpath(table)


def _normalize_operation(op: dict[str, Any], folder: str = "") -> dict[str, Any] | None:
    """Coerce an operation dict; return None if it's unsalvageable.

    ``folder`` is the dashboard's folder; used to resolve folder-relative
    ``table`` paths to vault-absolute paths so the UI can pass them straight
    to /vault/datatable without further resolution.
    """
    if not isinstance(op, dict):
        return None
    op_id = str(op.get("id") or "").strip()
    label = str(op.get("label") or "").strip()
    kind = str(op.get("kind") or "chat").strip().lower()
    if not op_id or not label or kind not in ("chat", "form"):
        return None
    out: dict[str, Any] = {
        "id": op_id,
        "label": label,
        "kind": kind,
        "prompt": str(op.get("prompt") or "").strip(),
        "order": int(op.get("order", 0)),
    }
    if op.get("icon"):
        out["icon"] = str(op["icon"])
    if kind == "form":
        out["table"] = _resolve_table_path(folder, str(op.get("table") or ""))
    if op.get("prefill") and isinstance(op["prefill"], dict):
        out["prefill"] = op["prefill"]
    return out


def _normalize_widget(w: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce a widget dict; return None if it's unsalvageable.

    Shape::

        {id, title, viz_type: bar|line|area|pie|donut|table|kpi,
         query: str, query_tables: [str], viz_config: dict,
         prompt: str (optional, kept for redesign),
         refresh: manual|daily, last_refreshed_at: ISO|null,
         size: sm|md|lg, order: int}
    """
    if not isinstance(w, dict):
        return None
    wid = str(w.get("id") or "").strip()
    title = str(w.get("title") or "").strip()
    viz_type = str(w.get("viz_type") or w.get("kind") or "").strip().lower()
    if viz_type in ("chart", "report", "list"):
        viz_type = "bar"
    if not wid or not title or viz_type not in _VIZ_TYPES:
        return None
    query = str(w.get("query") or "").strip()
    if not query:
        return None
    refresh = str(w.get("refresh") or "manual").strip().lower()
    if refresh not in _WIDGET_REFRESH:
        refresh = "manual"
    out: dict[str, Any] = {
        "id": wid,
        "title": title,
        "viz_type": viz_type,
        "query": query,
        "order": int(w.get("order", 0)),
    }
    qt = w.get("query_tables")
    if isinstance(qt, list):
        out["query_tables"] = [str(t) for t in qt if isinstance(t, str)]
    vc = w.get("viz_config")
    if isinstance(vc, dict):
        out["viz_config"] = vc
    prompt = str(w.get("prompt") or "").strip()
    if prompt:
        out["prompt"] = prompt
    out["refresh"] = refresh
    raw_size = w.get("size")
    if isinstance(raw_size, str) and raw_size.strip().lower() in _WIDGET_SIZES:
        out["size"] = raw_size.strip().lower()
    last = w.get("last_refreshed_at")
    if isinstance(last, str) and last:
        out["last_refreshed_at"] = last
    else:
        out["last_refreshed_at"] = None
    return out


def default_dashboard(folder: str) -> dict[str, Any]:
    """Return the implicit dashboard for a folder (no disk write)."""
    return {
        "folder": folder,
        "title": _folder_basename(folder),
        "chat_session_id": None,
        "operations": [],
        "widgets": [],
        "exists": False,
        "schema_version": SCHEMA_VERSION,
    }


def read_dashboard(folder: str) -> dict[str, Any]:
    """Read the folder's `_data.md`, falling back to `default_dashboard()`.

    Returns a dict with ``folder``, ``title``, ``chat_session_id``, ``operations``
    (sorted by ``order``), ``exists`` (True iff `_data.md` is present and parses
    as a dashboard file), and ``schema_version``.
    """
    path = dashboard_path(folder)
    try:
        file = vault.read_file(path)
    except (FileNotFoundError, OSError):
        return default_dashboard(folder)
    content = file.get("content", "")
    if not is_dashboard_file(content):
        return default_dashboard(folder)
    _, body = _extract_frontmatter(content)
    data = _extract_dashboard_yaml(body)
    raw_ops = data.get("operations")
    operations: list[dict[str, Any]] = []
    if isinstance(raw_ops, list):
        for op in raw_ops:
            norm = _normalize_operation(op, folder) if isinstance(op, dict) else None
            if norm is not None:
                operations.append(norm)
    operations.sort(key=lambda o: o.get("order", 0))
    raw_widgets = data.get("widgets")
    widgets: list[dict[str, Any]] = []
    if isinstance(raw_widgets, list):
        for w in raw_widgets:
            norm_w = _normalize_widget(w) if isinstance(w, dict) else None
            if norm_w is not None:
                widgets.append(norm_w)
    widgets.sort(key=lambda w: w.get("order", 0))
    return {
        "folder": folder,
        "title": str(data.get("title") or _folder_basename(folder)),
        "chat_session_id": data.get("chat_session_id") or None,
        "operations": operations,
        "widgets": widgets,
        "exists": True,
        "schema_version": int(data.get("schema_version") or SCHEMA_VERSION),
    }


def _serialize(dashboard: dict[str, Any]) -> str:
    fm = {DASHBOARD_PLUGIN_KEY: "basic"}
    body_yaml: dict[str, Any] = {
        "title": dashboard.get("title") or _folder_basename(dashboard.get("folder", "")),
        "chat_session_id": dashboard.get("chat_session_id"),
        "operations": dashboard.get("operations", []),
        "widgets": dashboard.get("widgets", []),
        "schema_version": SCHEMA_VERSION,
    }
    fm_text = yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip()
    body_text = yaml.dump(body_yaml, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    return (
        f"---\n{fm_text}\n---\n\n"
        f"## Dashboard\n```yaml\n{body_text}\n```\n"
    )


def write_dashboard(folder: str, dashboard: dict[str, Any]) -> dict[str, Any]:
    """Materialize the dashboard file. Returns the read-back dict."""
    path = dashboard_path(folder)
    payload = {
        "folder": folder,
        "title": dashboard.get("title") or _folder_basename(folder),
        "chat_session_id": dashboard.get("chat_session_id") or None,
        "operations": [
            op for op in (
                _normalize_operation(o, folder) if isinstance(o, dict) else None
                for o in (dashboard.get("operations") or [])
            )
            if op is not None
        ],
        "widgets": [
            w for w in (
                _normalize_widget(o) if isinstance(o, dict) else None
                for o in (dashboard.get("widgets") or [])
            )
            if w is not None
        ],
    }
    vault.write_file(path, _serialize(payload))
    return read_dashboard(folder)


def patch_dashboard(folder: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the existing dashboard, materializing the file.

    Recognised keys: ``title``, ``chat_session_id``, ``operations``.
    """
    current = read_dashboard(folder)
    merged: dict[str, Any] = {
        "folder": folder,
        "title": current.get("title"),
        "chat_session_id": current.get("chat_session_id"),
        "operations": current.get("operations", []),
        "widgets": current.get("widgets", []),
    }
    if "title" in patch and patch["title"] is not None:
        merged["title"] = str(patch["title"])
    if "chat_session_id" in patch:
        merged["chat_session_id"] = patch["chat_session_id"] or None
    if "operations" in patch and isinstance(patch["operations"], list):
        merged["operations"] = patch["operations"]
    if "widgets" in patch and isinstance(patch["widgets"], list):
        merged["widgets"] = patch["widgets"]
    return write_dashboard(folder, merged)


def upsert_operation(folder: str, op: dict[str, Any]) -> dict[str, Any]:
    """Append or replace an operation by id. Materializes the file."""
    norm = _normalize_operation(op, folder)
    if norm is None:
        raise ValueError("operation missing required fields (id, label, kind, valid kind)")
    if not _SLUG_RE.match(norm["id"]):
        raise ValueError(f"operation id {norm['id']!r} must be a slug")
    if norm["kind"] == "form" and not norm.get("table"):
        raise ValueError("operations with kind='form' must specify `table`")
    current = read_dashboard(folder)
    operations = [o for o in current.get("operations", []) if o.get("id") != norm["id"]]
    if "order" not in op:
        norm["order"] = len(operations)
    operations.append(norm)
    operations.sort(key=lambda o: o.get("order", 0))
    return patch_dashboard(folder, {"operations": operations})


def delete_operation(folder: str, op_id: str) -> dict[str, Any]:
    """Remove an operation by id. No-op if it doesn't exist (still writes)."""
    current = read_dashboard(folder)
    operations = [o for o in current.get("operations", []) if o.get("id") != op_id]
    return patch_dashboard(folder, {"operations": operations})


def set_chat_session(folder: str, session_id: str | None) -> dict[str, Any]:
    """Persist the chat session id bound to this database."""
    return patch_dashboard(folder, {"chat_session_id": session_id})


def upsert_widget(folder: str, widget: dict[str, Any]) -> dict[str, Any]:
    """Append or replace a widget by id. Materializes the file."""
    norm = _normalize_widget(widget)
    if norm is None:
        raise ValueError(
            "widget missing required fields (id, title, viz_type ∈ "
            f"{_VIZ_TYPES})"
        )
    if not _SLUG_RE.match(norm["id"]):
        raise ValueError(f"widget id {norm['id']!r} must be a slug")
    current = read_dashboard(folder)
    widgets = [w for w in current.get("widgets", []) if w.get("id") != norm["id"]]
    if "order" not in widget:
        norm["order"] = len(widgets)
    widgets.append(norm)
    widgets.sort(key=lambda w: w.get("order", 0))
    return patch_dashboard(folder, {"widgets": widgets})


def delete_widget(folder: str, widget_id: str) -> dict[str, Any]:
    """Remove a widget by id and delete its result file (if any)."""
    current = read_dashboard(folder)
    widgets = [w for w in current.get("widgets", []) if w.get("id") != widget_id]
    # Best-effort cleanup of the result file. Missing file is fine.
    try:
        from . import vault_widgets
        vault_widgets.delete_widget_result(folder, widget_id)
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return patch_dashboard(folder, {"widgets": widgets})


def set_widget_refreshed(
    folder: str, widget_id: str, last_refreshed_at: str
) -> dict[str, Any]:
    """Stamp ``last_refreshed_at`` on a widget. No-op if missing."""
    current = read_dashboard(folder)
    widgets = list(current.get("widgets", []))
    for w in widgets:
        if w.get("id") == widget_id:
            w["last_refreshed_at"] = last_refreshed_at
            break
    else:
        return current
    return patch_dashboard(folder, {"widgets": widgets})


def delete_database(folder: str, *, confirm: str) -> dict[str, Any]:
    """Permanently remove every file in ``folder`` (data-tables + `_data.md`).

    Caller must pass ``confirm`` equal to the folder's basename — guards
    accidental destruction by both human and agent callers. Returns
    ``{deleted: int, paths: [...]}``.
    """
    folder = (folder or "").strip("/")
    if not folder:
        raise ValueError("cannot delete the vault root via delete_database")
    expected = _folder_basename(folder)
    if confirm != expected:
        raise ValueError(
            f"confirm must equal folder basename {expected!r}, got {confirm!r}",
        )
    full = vault.resolve_path(folder)
    if not full.exists() or not full.is_dir():
        raise FileNotFoundError(f"no such folder: {folder!r}")
    deleted: list[str] = []
    # Remove every file via the regular vault.delete path so search/graph
    # indexes get the per-file invalidations they expect, then drop the
    # (now-empty) folder.
    for entry in list(vault.list_tree()):
        if entry.type != "file":
            continue
        rel = entry.path
        if rel == folder or rel.startswith(folder + "/"):
            try:
                vault.delete(rel)
                deleted.append(rel)
            except (FileNotFoundError, OSError):
                continue
    try:
        vault.delete(folder, recursive=True)
    except (FileNotFoundError, OSError):
        pass
    return {"deleted": len(deleted), "paths": deleted, "folder": folder}
