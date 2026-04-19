"""skill_manage tool — six actions for agent-authored skill lifecycle."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter  # type: ignore[import]

from .guard import scan
from .registry import SkillRegistry, _write_meta

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}
_MAX_SKILL_MD = 100_000
_MAX_FILE = 1024 * 1024


@dataclass
class ManagerResult:
    ok: bool
    message: str
    rolled_back: bool = False


class SkillManager:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry
        self._skills_dir = registry._dir

    def invoke(self, action: str, args: dict[str, Any]) -> ManagerResult:
        dispatch = {
            "create": self._create,
            "edit": self._edit,
            "patch": self._patch,
            "delete": self._delete,
            "write_file": self._write_file,
            "remove_file": self._remove_file,
        }
        fn = dispatch.get(action)
        if fn is None:
            return ManagerResult(ok=False, message=f"unknown action: {action!r}")
        return fn(args)

    def _create(self, args: dict[str, Any]) -> ManagerResult:
        name = args.get("name", "")
        content = args.get("content", "")
        if not _NAME_RE.match(name):
            return ManagerResult(ok=False, message=f"invalid skill name: {name!r}")
        if len(content) > _MAX_SKILL_MD:
            return ManagerResult(ok=False, message="SKILL.md content exceeds 100k chars")
        skill_dir = self._skills_dir / name
        if skill_dir.exists():
            return ManagerResult(ok=False, message=f"skill {name!r} already exists; use edit")
        result = self._validate_frontmatter(content, name)
        if result is not None:
            return result
        guard = scan(content)
        if guard.level == "dangerous":
            return ManagerResult(
                ok=False,
                message=f"skill blocked by guard: {[f.pattern for f in guard.findings]}",
                rolled_back=True,
            )
        skill_dir.mkdir(parents=True)
        _atomic_write(skill_dir / "SKILL.md", content)
        _write_meta(skill_dir, trust="agent", authored_at=_now())
        self._registry.reload()
        return ManagerResult(ok=True, message=f"skill {name!r} created")

    def _edit(self, args: dict[str, Any]) -> ManagerResult:
        name = args.get("name", "")
        content = args.get("content", "")
        if len(content) > _MAX_SKILL_MD:
            return ManagerResult(ok=False, message="content exceeds 100k chars")
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            return ManagerResult(ok=False, message=f"skill {name!r} not found")
        result = self._validate_frontmatter(content, name)
        if result is not None:
            return result
        guard = scan(content)
        if guard.level == "dangerous":
            return ManagerResult(
                ok=False,
                message=f"edit blocked by guard: {[f.pattern for f in guard.findings]}",
                rolled_back=True,
            )
        _atomic_write(skill_dir / "SKILL.md", content)
        self._registry.reload()
        return ManagerResult(ok=True, message=f"skill {name!r} updated")

    def _patch(self, args: dict[str, Any]) -> ManagerResult:
        name = args.get("name", "")
        old = args.get("old", "")
        new = args.get("new", "")
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            return ManagerResult(ok=False, message=f"skill {name!r} not found")
        skill_md = skill_dir / "SKILL.md"
        current = skill_md.read_text(encoding="utf-8")
        if old not in current:
            return ManagerResult(ok=False, message="old string not found in SKILL.md")
        patched = current.replace(old, new, 1)
        guard = scan(patched)
        if guard.level == "dangerous":
            return ManagerResult(
                ok=False,
                message=f"patch blocked by guard: {[f.pattern for f in guard.findings]}",
                rolled_back=True,
            )
        _atomic_write(skill_md, patched)
        self._registry.reload()
        return ManagerResult(ok=True, message=f"skill {name!r} patched")

    def _delete(self, args: dict[str, Any]) -> ManagerResult:
        name = args.get("name", "")
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            return ManagerResult(ok=False, message=f"skill {name!r} not found")
        import shutil
        shutil.rmtree(skill_dir)
        self._registry.reload()
        return ManagerResult(ok=True, message=f"skill {name!r} deleted")

    def _write_file(self, args: dict[str, Any]) -> ManagerResult:
        name = args.get("name", "")
        rel_path = args.get("path", "")
        content = args.get("content", "")
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            return ManagerResult(ok=False, message=f"skill {name!r} not found")
        err = _check_path(rel_path, skill_dir)
        if err:
            return ManagerResult(ok=False, message=err)
        if len(content) > _MAX_FILE:
            return ManagerResult(ok=False, message="file content exceeds 1 MiB")
        target = skill_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        return ManagerResult(ok=True, message=f"wrote {rel_path} in skill {name!r}")

    def _remove_file(self, args: dict[str, Any]) -> ManagerResult:
        name = args.get("name", "")
        rel_path = args.get("path", "")
        skill_dir = self._skills_dir / name
        if not skill_dir.is_dir():
            return ManagerResult(ok=False, message=f"skill {name!r} not found")
        err = _check_path(rel_path, skill_dir)
        if err:
            return ManagerResult(ok=False, message=err)
        target = skill_dir / rel_path
        if not target.exists():
            return ManagerResult(ok=False, message=f"{rel_path} not found")
        target.unlink()
        return ManagerResult(ok=True, message=f"removed {rel_path} from skill {name!r}")

    def _validate_frontmatter(self, content: str, expected_name: str) -> ManagerResult | None:
        try:
            post = frontmatter.loads(content)
        except Exception as exc:
            return ManagerResult(ok=False, message=f"YAML parse error: {exc}")
        if not post.metadata.get("name"):
            return ManagerResult(ok=False, message="SKILL.md must have a name in frontmatter")
        if not post.metadata.get("description"):
            return ManagerResult(ok=False, message="SKILL.md must have a description in frontmatter")
        if post.metadata.get("name") != expected_name:
            return ManagerResult(ok=False, message=f"frontmatter name must match skill dir: {expected_name!r}")
        return None


def _atomic_write(path: Path, content: str) -> None:
    dir_ = path.parent
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _check_path(rel_path: str, skill_dir: Path) -> str | None:
    if ".." in rel_path or rel_path.startswith("/"):
        return "path traversal not allowed"
    parts = Path(rel_path).parts
    if parts and parts[0] not in _ALLOWED_SUBDIRS:
        return f"first path component must be one of: {sorted(_ALLOWED_SUBDIRS)}"
    resolved = (skill_dir / rel_path).resolve()
    if not str(resolved).startswith(str(skill_dir.resolve())):
        return "path escapes skill directory"
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
