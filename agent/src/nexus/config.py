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
FRONTEND_DIR: Path = Path(_env("NEXUS_FRONTEND_DIR", "")).expanduser() if _env("NEXUS_FRONTEND_DIR", "") else Path(__file__).resolve().parent.parent.parent.parent / "ui"


def get_frontend_dir() -> Path | None:
    d = FRONTEND_DIR
    if d.exists() and (d / "package.json").exists():
        return d
    return None
