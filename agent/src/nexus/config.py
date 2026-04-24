"""Env-driven config for Nexus."""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


LLM_BASE_URL: str = _env("NEXUS_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY: str = _env("NEXUS_LLM_API_KEY", "")
LLM_MODEL: str = _env("NEXUS_LLM_MODEL", "gpt-4o-mini")
SKILLS_DIR: Path = Path(_env("NEXUS_SKILLS_DIR", "~/.nexus/skills")).expanduser()
PORT: int = int(_env("NEXUS_PORT", "18989"))
# Package-relative default — works when running from a source checkout
# (agent/src/nexus/config.py → ../../../../ui). Falls back to None when
# installed via ``uv tool`` (no ui/ shipped with the package).
_PACKAGE_RELATIVE_FRONTEND: Path = (
    Path(__file__).resolve().parent.parent.parent.parent / "ui"
)
FRONTEND_DIR: Path = (
    Path(_env("NEXUS_FRONTEND_DIR", "")).expanduser()
    if _env("NEXUS_FRONTEND_DIR", "")
    else _PACKAGE_RELATIVE_FRONTEND
)


def _looks_like_ui_dir(d: Path) -> bool:
    try:
        return d.is_dir() and (d / "package.json").is_file()
    except OSError:
        return False


def _iter_candidate_frontend_dirs() -> list[Path]:
    """Candidate paths to search, in priority order.

    1. Explicit NEXUS_FRONTEND_DIR env var (honoured by ``FRONTEND_DIR``).
    2. Package-relative ``../../../ui`` — only meaningful when running from
       a source checkout; a no-op under ``uv tool`` installs.
    3. An override the user persisted in ``~/.nexus/config.toml`` under
       ``[agent] frontend_dir``.
    4. Common checkout locations (~/Code/nexus/ui, ~/code/nexus/ui,
       ~/src/nexus/ui, ~/projects/nexus/ui, ~/dev/nexus/ui).
    5. Walk the current working directory upward looking for a sibling
       ``ui/`` folder (handy when the user just ``cd`` into their checkout
       and runs ``nexus serve`` from any subdirectory).
    """
    cands: list[Path] = [FRONTEND_DIR]

    # [3] Config file override — read lazily so this module stays importable
    # during config bootstrap, and so we don't circular-import config_file.
    try:
        from .config_file import load as _load_cfg  # type: ignore[import-not-found]
        cfg = _load_cfg()
        frontend_dir = getattr(getattr(cfg, "agent", None), "frontend_dir", None)
        if frontend_dir:
            cands.append(Path(frontend_dir).expanduser())
    except Exception:
        pass

    # [4] Common checkout locations.
    home = Path.home()
    for base in (
        home / "Code" / "nexus",
        home / "code" / "nexus",
        home / "src" / "nexus",
        home / "projects" / "nexus",
        home / "dev" / "nexus",
        home / "Documents" / "Code" / "nexus",
    ):
        cands.append(base / "ui")

    # [5] Walk up from cwd to the filesystem root.
    try:
        cwd = Path.cwd()
    except (FileNotFoundError, OSError):
        cwd = None
    if cwd is not None:
        for p in [cwd, *cwd.parents]:
            cands.append(p / "ui")
            # Also handle the case where the user cd'd into a sibling repo
            # (e.g. running `nexus` from inside `loom/`) and a `nexus/ui/`
            # exists next door.
            cands.append(p / "nexus" / "ui")

    # Dedupe while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for c in cands:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            unique.append(c)
    return unique


def get_frontend_dir() -> Path | None:
    for cand in _iter_candidate_frontend_dirs():
        if _looks_like_ui_dir(cand):
            return cand
    return None
