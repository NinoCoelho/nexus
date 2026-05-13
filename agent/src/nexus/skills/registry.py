"""Skill registry — loads SKILL.md files from disk and maintains an index."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

import frontmatter  # type: ignore[import]

from .types import DerivedFrom, DerivedFromSource, KeyRequirement, Skill

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Dev-checkout default: <repo>/skills, five levels up from this file.
# The packaged .app overrides this via NEXUS_BUILTIN_SKILLS_DIR (set by
# bootstrap.py) because the bundle layout doesn't match the repo layout.
_BUNDLED_SKILLS_DIR = Path(__file__).parent.parent.parent.parent.parent / "skills"
_SEEDED_MARKER = ".seeded-builtins.json"


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


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
        self._sync_builtins()
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
                _seed_venv(dest, name)
            seeded.add(name)
            changed = True

        if changed or self._load_seeded_marker() is None:
            self._write_seeded_marker(seeded)

    def _sync_builtins(self) -> None:
        """Sync files from bundled builtins into already-installed skills.

        Compares ``requirements.txt`` (and other managed files) between the
        bundled source and the installed copy.  If the bundled version differs,
        the file is re-copied and the skill's venv is re-synced.  SKILL.md
        and user-authored files (scripts/, references/, etc.) are **not**
        touched — users may have local edits.
        """
        bundled = _bundled_skills_dir()
        if not bundled.is_dir():
            return

        meta = self._load_seeded_marker()
        if meta is None:
            return

        from .venv_manager import ensure_venv

        for child in bundled.iterdir():
            if not (child / "SKILL.md").is_file():
                continue
            name = child.name
            if name not in meta:
                continue
            dest = self._dir / name
            if not dest.is_dir():
                continue

            req_src = child / "requirements.txt"
            req_dst = dest / "requirements.txt"
            if not req_src.is_file():
                continue

            src_hash = _file_hash(req_src)
            dst_hash = _file_hash(req_dst) if req_dst.is_file() else None

            if src_hash == dst_hash:
                continue

            shutil.copy2(req_src, req_dst)
            log.info("synced requirements.txt for builtin skill: %s", name)

            try:
                skill = _load_skill(dest)
            except Exception:
                continue
            try:
                ensure_venv(name, dest, skill.python_version)
            except Exception as exc:
                log.warning("failed to sync venv for %s: %s", name, exc)

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
    requires_keys = _parse_requires_keys(post.metadata.get("requires_keys"))
    python_version: str | None = post.metadata.get("python_version")
    if python_version is not None:
        python_version = str(python_version)
    has_requirements = (skill_dir / "requirements.txt").is_file()
    meta = _read_meta(skill_dir)
    trust = meta.get("trust", "user")
    derived_from = _parse_derived_from(meta.get("derived_from"))
    return Skill(
        name=name,
        description=description,
        body=post.content,
        source_dir=skill_dir,
        trust=trust,
        requires_keys=requires_keys,
        derived_from=derived_from,
        python_version=python_version,
        has_requirements=has_requirements,
    )


def _parse_derived_from(raw: Any) -> DerivedFrom | None:
    """Coerce ``.meta.json`` ``derived_from`` into a :class:`DerivedFrom`.

    Returns ``None`` for missing / malformed data so a corrupt sidecar
    doesn't prevent the skill from loading.
    """
    if not isinstance(raw, dict):
        return None
    raw_sources = raw.get("sources") or []
    if not isinstance(raw_sources, list):
        raw_sources = []
    sources: list[DerivedFromSource] = []
    for entry in raw_sources:
        if not isinstance(entry, dict):
            continue
        sources.append(
            DerivedFromSource(
                slug=str(entry.get("slug", ""))[:128],
                url=str(entry.get("url", ""))[:512],
                title=str(entry.get("title", ""))[:200],
            )
        )
    return DerivedFrom(
        wizard_ask=str(raw.get("wizard_ask", ""))[:500],
        wizard_built_at=str(raw.get("wizard_built_at", ""))[:64],
        sources=tuple(sources),
    )


def _parse_requires_keys(raw: Any) -> tuple[KeyRequirement, ...]:
    """Accept either ``[STRING]`` or ``[{name, help?, url?}]``.

    Raises ValueError on malformed entries so the registry can skip the
    skill with a clear log line rather than silently dropping the
    requirement.
    """
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, list):
        raise ValueError("requires_keys must be a list")
    out: list[KeyRequirement] = []
    for entry in raw:
        if isinstance(entry, str):
            key_name = entry
            req = KeyRequirement(name=key_name)
        elif isinstance(entry, dict):
            key_name = entry.get("name") or ""
            req = KeyRequirement(
                name=key_name,
                help=entry.get("help"),
                url=entry.get("url"),
            )
        else:
            raise ValueError(
                f"requires_keys entries must be strings or objects, got {type(entry).__name__}"
            )
        if not _KEY_NAME_RE.match(req.name):
            raise ValueError(
                f"requires_keys[].name must match ^[A-Z][A-Z0-9_]*$, got {req.name!r}"
            )
        out.append(req)
    return tuple(out)


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


def _seed_venv(skill_dir: Path, name: str) -> None:
    """Eagerly create a per-skill venv during seeding if requirements.txt exists."""
    req = skill_dir / "requirements.txt"
    if not req.is_file():
        return
    try:
        post = frontmatter.loads((skill_dir / "SKILL.md").read_text())
    except Exception:
        post = None
    python_version = None
    if post is not None:
        pv = post.metadata.get("python_version")
        if pv is not None:
            python_version = str(pv)
    from .venv_manager import ensure_venv

    try:
        ensure_venv(name, skill_dir, python_version)
    except Exception as exc:
        log.warning("failed to create venv for seeded skill %s: %s", name, exc)
