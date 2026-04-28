"""Skill registry — loads SKILL.md files from disk and maintains an index."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

import frontmatter  # type: ignore[import]

from .types import Skill

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# Dev-checkout default: <repo>/skills, five levels up from this file.
# The packaged .app overrides this via NEXUS_BUILTIN_SKILLS_DIR (set by
# bootstrap.py) because the bundle layout doesn't match the repo layout.
_BUNDLED_SKILLS_DIR = Path(__file__).parent.parent.parent.parent.parent / "skills"
_SEEDED_MARKER = ".seeded-builtins.json"


def _bundled_skills_dir() -> Path:
    override = os.environ.get("NEXUS_BUILTIN_SKILLS_DIR")
    if override:
        return Path(override)
    return _BUNDLED_SKILLS_DIR


class SkillRegistry:
    """Scans NEXUS_SKILLS_DIR for SKILL.md subdirectories."""

    def __init__(self, skills_dir: Path) -> None:
        self._dir = skills_dir
        self._by_name: dict[str, Skill] = {}
        self._ensure_dir()
        self._seed_new_builtins()
        self.reload()

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _seed_new_builtins(self) -> None:
        """Copy bundled skills that have never been seeded into the user dir.

        A marker file records names that were previously seeded so deletions
        and user modifications survive across upgrades. Existing installs
        that predate the marker are migrated by treating any currently
        installed skill as already-seeded.
        """
        bundled = _bundled_skills_dir()
        if not bundled.is_dir():
            return

        seeded = self._load_seeded_marker()
        if seeded is None:
            seeded = {child.name for child in self._dir.iterdir() if child.is_dir()}

        changed = False
        for child in bundled.iterdir():
            if not (child / "SKILL.md").is_file():
                continue
            name = child.name
            if name in seeded:
                continue
            dest = self._dir / name
            if not dest.exists():
                shutil.copytree(child, dest, dirs_exist_ok=True)
                _write_meta(dest, trust="builtin")
                log.info("seeded builtin skill: %s", name)
            seeded.add(name)
            changed = True

        if changed or self._load_seeded_marker() is None:
            self._write_seeded_marker(seeded)

    def _seeded_marker_path(self) -> Path:
        return self._dir / _SEEDED_MARKER

    def _load_seeded_marker(self) -> set[str] | None:
        path = self._seeded_marker_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text())
            return set(data.get("seeded", []))
        except Exception:
            return None

    def _write_seeded_marker(self, seeded: set[str]) -> None:
        path = self._seeded_marker_path()
        path.write_text(json.dumps({"seeded": sorted(seeded)}, indent=2))

    def reload(self) -> None:
        self._by_name = {}
        for child in sorted(self._dir.iterdir()):
            if not (child / "SKILL.md").is_file():
                continue
            try:
                skill = _load_skill(child)
                self._by_name[skill.name] = skill
            except Exception as exc:
                log.warning("skipping malformed skill %s: %s", child, exc)

    def list(self) -> list[Skill]:
        return sorted(self._by_name.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill:
        if name not in self._by_name:
            raise KeyError(f"no such skill: {name!r}")
        return self._by_name[name]

    def descriptions(self) -> list[tuple[str, str]]:
        """(name, description) pairs for system prompt injection."""
        return [(s.name, s.description) for s in self.list()]

    def __contains__(self, name: str) -> bool:
        return name in self._by_name


def _load_skill(skill_dir: Path) -> Skill:
    skill_md = skill_dir / "SKILL.md"
    raw = skill_md.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    name: str = post.metadata.get("name", "")
    description: str = post.metadata.get("description", "")
    if not name:
        raise ValueError("missing name in frontmatter")
    if not description:
        raise ValueError("missing description in frontmatter")
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid skill name: {name!r}")
    trust = _read_meta(skill_dir).get("trust", "user")
    return Skill(
        name=name,
        description=description,
        body=post.content,
        source_dir=skill_dir,
        trust=trust,
    )


def _read_meta(skill_dir: Path) -> dict[str, Any]:
    meta_path = skill_dir / ".meta.json"
    if meta_path.is_file():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            pass
    return {}


def _write_meta(skill_dir: Path, **kwargs: Any) -> None:
    meta_path = skill_dir / ".meta.json"
    existing = _read_meta(skill_dir)
    existing.update(kwargs)
    meta_path.write_text(json.dumps(existing, indent=2))
