from __future__ import annotations

import re
import uuid
from typing import Any

import yaml

from . import vault

DATATABLE_PLUGIN_KEY = "data-table-plugin"

_FENCE_RE = re.compile(
    r"```ya?ml\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_SCHEMA_SECTION = re.compile(r"^##\s+Schema\s*$", re.MULTILINE | re.IGNORECASE)
_ROWS_SECTION = re.compile(r"^##\s+Rows\s*$", re.MULTILINE | re.IGNORECASE)
_VIEWS_SECTION = re.compile(r"^##\s+Views\s*$", re.MULTILINE | re.IGNORECASE)


def is_datatable_file(content: str) -> bool:
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and DATATABLE_PLUGIN_KEY in fm


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


def _extract_section_yaml(body: str, section_re: re.Pattern) -> tuple[Any, int, int]:
    m_sec = section_re.search(body)
    if not m_sec:
        return None, -1, -1
    after = body[m_sec.end():]
    m_fence = _FENCE_RE.search(after)
    if not m_fence:
        return None, -1, -1
    raw = m_fence.group(1)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        parsed = None
    fence_start = m_sec.end() + m_fence.start()
    fence_end = m_sec.end() + m_fence.end()
    return parsed, fence_start, fence_end


def read_table(path: str) -> dict[str, Any]:
    file = vault.read_file(path)
    content = file["content"]
    _, body = _extract_frontmatter(content)

    schema_raw, _, _ = _extract_section_yaml(body, _SCHEMA_SECTION)
    rows_raw, _, _ = _extract_section_yaml(body, _ROWS_SECTION)
    views_raw, _, _ = _extract_section_yaml(body, _VIEWS_SECTION)

    schema: dict[str, Any] = {}
    if isinstance(schema_raw, dict):
        schema = schema_raw

    rows: list[dict[str, Any]] = []
    if isinstance(rows_raw, list):
        for r in rows_raw:
            if isinstance(r, dict):
                if "_id" not in r:
                    r = {"_id": uuid.uuid4().hex[:8], **r}
                rows.append(r)

    views: list[dict[str, Any]] = []
    if isinstance(views_raw, list):
        views = [v for v in views_raw if isinstance(v, dict)]

    return {"schema": schema, "rows": rows, "views": views}


def _serialize(
    frontmatter: dict[str, Any],
    schema: dict[str, Any],
    rows: list[dict[str, Any]],
    views: list[dict[str, Any]] | None = None,
) -> str:
    fm_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip()
    schema_yaml = yaml.dump(schema, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    rows_yaml = yaml.dump(rows, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    out = (
        f"---\n{fm_text}\n---\n\n"
        f"## Schema\n```yaml\n{schema_yaml}\n```\n\n"
        f"## Rows\n```yaml\n{rows_yaml}\n```\n"
    )
    if views:
        views_yaml = yaml.dump(views, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
        out += f"\n## Views\n```yaml\n{views_yaml}\n```\n"
    return out


def _load_state(path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    file = vault.read_file(path)
    fm, _ = _extract_frontmatter(file["content"])
    fm.setdefault(DATATABLE_PLUGIN_KEY, "basic")
    return fm, read_table(path)


def _ref_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        return []
    return [f for f in fields if isinstance(f, dict) and f.get("kind") == "ref"]


def _rollup_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        return []
    return [f for f in fields if isinstance(f, dict) and f.get("kind") == "rollup"]
