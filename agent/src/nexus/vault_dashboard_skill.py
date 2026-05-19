"""Auto-generate per-database skills so the agent has instant context.

Every database folder (one with a ``_data.md``) gets a companion skill
named ``db-<folder>`` in ``~/.nexus/skills/``. The skill body summarises
all tables, operations, screens, flows, and links — the agent loads it
on demand and immediately knows the schema without discovery calls.

Lifecycle:

* ``sync_skill(folder, dashboard)`` — called after every dashboard write.
  Creates or updates the skill to match the current dashboard state.
* ``delete_skill(folder)`` — called from ``delete_database``. Removes the
  companion skill.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

_SKILLS_DIR = Path.home() / ".nexus" / "skills"
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _skill_name(folder: str) -> str:
    slug = folder.strip("/").replace("/", "-").replace("_", "-").lower()
    name = f"db-{slug}" if slug else "db-root"
    return name


def _tables_in_folder(folder: str) -> list[dict[str, Any]]:
    import nexus.vault as vault

    tables: list[dict[str, Any]] = []
    for entry in vault.list_tree():
        if entry.type != "file" or not entry.path.endswith(".md"):
            continue
        if entry.path.startswith(folder + "/") and entry.path != f"{folder}/_data.md":
            try:
                raw = vault.read_file(entry.path)
                if "data-table-plugin" not in (raw or "")[:200]:
                    continue
                parsed = _parse_table_md(raw)
                rel = entry.path.removeprefix(folder + "/")
                parsed["_file"] = rel
                tables.append(parsed)
            except Exception:
                pass
    return tables


def _parse_table_md(content: str) -> dict[str, Any]:
    try:
        parts = content.split("```yaml")
        if len(parts) < 2:
            return {}
        body = parts[1].split("```")[0]
        data = yaml.safe_load(body)
        if not isinstance(data, dict):
            return {}
        result: dict[str, Any] = {}
        if "table" in data:
            result["table_meta"] = data["table"]
        if "fields" in data and isinstance(data["fields"], list):
            result["fields"] = data["fields"]
        if "rows" in data and isinstance(data["rows"], list):
            result["row_count"] = len(data["rows"])
        return result
    except Exception:
        return {}


def _build_body(folder: str, dashboard: dict[str, Any]) -> str:
    tables = _tables_in_folder(folder)
    title = dashboard.get("title") or folder

    lines = [
        f"# {title} — Database Reference",
        "",
        f"Auto-generated skill for the **{folder}** database. "
        "Loaded when the user asks about or works with this app.",
        "",
    ]

    if tables:
        lines.append("## Tables")
        lines.append("")
        for tbl in tables:
            fname = tbl.get("_file", "???")
            meta = tbl.get("table_meta", {})
            pk = meta.get("primary_key", "_id")
            fields = tbl.get("fields", [])
            row_count = tbl.get("row_count", 0)
            lines.append(f"### `{fname}` ({row_count} rows, PK: {pk})")
            lines.append("")
            if fields:
                lines.append("| Field | Kind | Notes |")
                lines.append("|-------|------|-------|")
                for f in fields:
                    if not isinstance(f, dict):
                        continue
                    name = f.get("name", "")
                    kind = f.get("kind", "text")
                    notes: list[str] = []
                    if f.get("required"):
                        notes.append("required")
                    target = f.get("target_table", "")
                    if target:
                        card = f.get("cardinality", "one")
                        notes.append(f"ref → {target} ({card})")
                    choices = f.get("choices", [])
                    if choices:
                        notes.append(f"choices: {', '.join(str(c) for c in choices[:5])}")
                    lines.append(f"| {name} | {kind} | {'; '.join(notes)} |")
                lines.append("")

    ops = dashboard.get("operations", [])
    if ops:
        lines.append("## Operations")
        lines.append("")
        for op in ops:
            if not isinstance(op, dict):
                continue
            oid = op.get("id", "")
            label = op.get("label", oid)
            kind = op.get("kind", "chat")
            table = op.get("table", "")
            prompt = op.get("prompt", "")
            desc = f"kind: {kind}"
            if table:
                desc += f", table: {table}"
            if prompt:
                desc += f", prompt: {prompt[:100]}"
            lines.append(f"- **{label}** (`{oid}`) — {desc}")
        lines.append("")

    screens = dashboard.get("screens", [])
    if screens:
        lines.append("## Screens")
        lines.append("")
        for sc in screens:
            if not isinstance(sc, dict):
                continue
            sid = sc.get("id", "")
            sname = sc.get("name", sid)
            layout = sc.get("layout", "unknown")
            sections = sc.get("sections", [])
            sec_desc = ", ".join(
                str(s.get("source", {}).get("table", s.get("id", "?"))) if isinstance(s, dict) else "?"
                for s in sections
            )
            lines.append(f"- **{sname}** (`{sid}`, {layout}) — sections: {sec_desc}")
        lines.append("")

    flows = dashboard.get("flows", [])
    if flows:
        lines.append("## Flows")
        lines.append("")
        for fl in flows:
            if not isinstance(fl, dict):
                continue
            fid = fl.get("id", "")
            fname = fl.get("name", fid)
            steps = fl.get("steps", [])
            step_desc = " → ".join(
                str(s.get("type", "?")) if isinstance(s, dict) else "?" for s in steps
            ) if steps else "no steps"
            lines.append(f"- **{fname}** (`{fid}`) — {step_desc}")
        lines.append("")

    links = dashboard.get("links", {})
    boards = links.get("boards", []) if isinstance(links, dict) else []
    calendars = links.get("calendars", []) if isinstance(links, dict) else []
    if boards or calendars:
        lines.append("## Linked Resources")
        lines.append("")
        for b in boards:
            lines.append(f"- Board: {b}")
        for c in calendars:
            lines.append(f"- Calendar: {c}")
        lines.append("")

    lines.append("## Quick Reference")
    lines.append("")
    lines.append(f"- Folder: `{folder}/`")
    lines.append(f"- Dashboard: `{folder}/_data.md`")

    table_names = [t.get("_file", "") for t in tables]
    if table_names:
        lines.append(f"- Tables: {', '.join(f'`{t}`' for t in table_names)}")

    lines.append("")
    lines.append("When the user asks to work with this app, use `datatable_manage` and "
                 "`dashboard_manage` with these paths. For form entry, use the operations above. "
                 "For parent-child workflows, use the flows above.")
    return "\n".join(lines)


def sync_skill(folder: str, dashboard: dict[str, Any]) -> None:
    name = _skill_name(folder)
    if not _SKILL_NAME_RE.match(name):
        return

    body = _build_body(folder, dashboard)
    title = dashboard.get("title") or folder
    frontmatter = (
        "---\n"
        f"name: {name}\n"
        f"description: \"Reference for the {title} database — tables, operations, screens, flows. "
        f"Loaded when the user works with this app.\"\n"
        "type: procedure\n"
        "role: reference\n"
        "platform: nexus\n"
        "nexus_status: stable\n"
        "nexus_authored_by: agent\n"
        "---\n"
    )
    content = frontmatter + "\n" + body

    skill_dir = _SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists() and skill_md.read_text() == content:
        return

    skill_md.write_text(content)

    meta_path = skill_dir / ".meta.json"
    meta_path.write_text(json.dumps({
        "trust": "agent",
        "authored_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "auto_generated": True,
        "source_folder": folder,
    }, indent=2))


def delete_skill(folder: str) -> None:
    import logging
    import shutil

    log = logging.getLogger(__name__)
    name = _skill_name(folder)
    skill_dir = _SKILLS_DIR / name
    if not skill_dir.is_dir():
        log.debug("delete_skill: no companion skill dir for %r (expected %s)", folder, skill_dir)
        return
    try:
        from .skills.venv_manager import remove_venv
        remove_venv(name)
    except Exception:
        pass
    shutil.rmtree(skill_dir)
    log.info("deleted companion skill %r for database %r", name, folder)
