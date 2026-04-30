"""Skill model for Nexus."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class KeyRequirement(BaseModel):
    """A credential a skill needs to function.

    Drives the first-use prompt: when the agent runs ``skill_view`` and a
    declared key is neither in the env nor in ``~/.nexus/secrets.toml``,
    the user is prompted via a masked HITL form. ``help`` and ``url``
    customize the prompt copy and the "Get it here →" link.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    help: str | None = None
    url: str | None = None


class Skill(BaseModel):
    """A single skill loaded from disk."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    body: str
    source_dir: Path
    trust: Literal["builtin", "user", "agent"] = "user"
    requires_keys: tuple[KeyRequirement, ...] = ()
